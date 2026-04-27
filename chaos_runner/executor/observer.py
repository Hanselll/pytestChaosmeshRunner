# -*- coding: utf-8 -*-
import json
import re
import shlex
import subprocess
import getpass
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from datetime import datetime, timezone, timedelta

from chaos_runner import yaml_compat as yaml

from chaos_runner import config
import chaos_runner.discover.ddb as ddb_discover
import chaos_runner.discover.rc as rc_discover
from chaos_runner.tools.k8s import sh, exec_in_pod
from chaos_runner.tools.pty_lmt import run_lmt_commands_in_container, run_lmt_commands_in_container_remote
from chaos_runner.tools.remote import is_remote_apply_enabled


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_ERROR_LOG_RE = re.compile(r"(?i)\b(error|exception|fatal|panic)\b")
_LOG_TS_RE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?)"
)
_POST_LOG_TAIL_LINES = 120
_POST_LOG_MAX_LINES_PER_POD = 80
_SMF_INTERNAL_LOG_DIR = "/var/log/service-logs"
_SMF_INTERNAL_LOG_FILES = 3
_SMF_INTERNAL_LOG_TAIL_LINES = 40
_DUPF_INTERNAL_LOG_DIR = "/var/log/service-logs"
_DUPF_INTERNAL_LOG_FILES = 4
_DUPF_INTERNAL_LOG_TAIL_LINES = 60
_DUPF_MQ_HOST_LOG_DIR = "/var/ctin/ctc-upf/mq-proxy"
_DUPF_MQ_HOST_LOG_FILES = 4
_DUPF_MQ_HOST_LOG_TAIL_LINES = 60


def _ts_ms():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class CaseLogger(object):
    def __init__(self, path, echo_stdout=True, mirror_paths=None):
        self.path = path
        self.echo_stdout = bool(echo_stdout)
        self.mirror_paths = [p for p in (mirror_paths or []) if p]
        self._lock = Lock()

    def _write_line(self, path, line):
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def log(self, msg):
        text = str(msg).replace("\r\n", "\n").replace("\r", "\n")
        text = _ANSI_ESCAPE_RE.sub("", text)
        text = _CONTROL_CHAR_RE.sub("", text)
        rows = text.split("\n") or [""]
        with self._lock:
            for row in rows:
                line = "[{}] {}".format(_ts_ms(), row)
                if self.echo_stdout:
                    print(line, flush=True)
                self._write_line(self.path, line)
                for path in self.mirror_paths:
                    self._write_line(path, line)


def extract_podchaos_target_pods(wf_yaml_text, namespace):
    """Extract pods that are selected by PodChaos templates only."""
    doc = yaml.safe_load(wf_yaml_text) or {}
    out = set()
    templates = ((doc.get("spec") or {}).get("templates") or [])
    for tpl in templates:
        if (tpl or {}).get("templateType") != "PodChaos":
            continue
        podchaos = (tpl.get("podChaos") or {})
        pods = ((podchaos.get("selector") or {}).get("pods") or {}).get(namespace)
        if isinstance(pods, list):
            for p in pods:
                if isinstance(p, str) and p:
                    out.add(p)
    return sorted(out)


def extract_target_pods_from_resolved(resolved):
    """Extract pod names from resolved target outputs for role-state scope."""
    out = set()
    for v in (resolved or {}).values():
        if isinstance(v, dict):
            pod = v.get("pod")
            if pod:
                out.add(pod)
            continue
        if isinstance(v, list):
            for it in v:
                if isinstance(it, dict) and it.get("pod"):
                    out.add(it.get("pod"))
    return sorted(out)


def _get_all_pod_items(namespace):
    data = json.loads(sh("kubectl -n {} get pod -o json".format(namespace)))
    return data.get("items", []) or []


def _build_pod_status_map(pod_items, pod_names=None):
    out = {}
    wanted = set(pod_names or [])
    filter_enabled = pod_names is not None
    for it in (pod_items or []):
        name = ((it.get("metadata") or {}).get("name") or "").strip()
        if filter_enabled and name not in wanted:
            continue
        if not name:
            continue
        status = ((it.get("status") or {}).get("phase") or "")
        node = ((it.get("spec") or {}).get("nodeName") or "")
        out[name] = {"status": status, "node": node}
    return out


def _get_pod_status_map(namespace, pod_names, pod_items=None):
    return _build_pod_status_map(pod_items if pod_items is not None else _get_all_pod_items(namespace), pod_names=pod_names)


def _get_all_pod_status_map(namespace):
    return _build_pod_status_map(_get_all_pod_items(namespace))


def _get_all_pods(namespace, pod_items=None):
    return list(pod_items if pod_items is not None else _get_all_pod_items(namespace))


def _component_of_pod(name):
    low = (name or "").lower()
    if "ddb" in low:
        return "ddb"
    if "etcd" in low:
        return "etcd"
    if "registry" in low or "-rc-" in low or "dupf-rc" in low:
        return "rc"
    if "mq" in low:
        return "mq"
    if "upc" in low or "upu" in low:
        return "upc"
    if "sdb" in low:
        return "sdb"
    return "other"


def _find_oam_pod(namespace, pod_items=None):
    for it in _get_all_pods(namespace, pod_items=pod_items):
        name = ((it.get("metadata") or {}).get("name") or "")
        deletion_ts = ((it.get("metadata") or {}).get("deletionTimestamp") or "")
        if "oam" in name and not deletion_ts:
            return name
    raise RuntimeError("Cannot find oam pod in {}".format(namespace))


def _all_active_pods(namespace, pod_items=None):
    out = []
    for it in _get_all_pods(namespace, pod_items=pod_items):
        name = ((it.get("metadata") or {}).get("name") or "").strip()
        deletion_ts = ((it.get("metadata") or {}).get("deletionTimestamp") or "")
        if name and not deletion_ts:
            out.append(name)
    return out


def _resolve_lmt_target_mode(namespace):
    mode = str(getattr(config, "LMT_TARGET_MODE", "auto") or "auto").strip().lower()
    if mode in ("oam_container", "dedicated_pod"):
        return mode
    ns = str(namespace or "").strip().lower()
    if ns.startswith("ns-smf"):
        return "dedicated_pod"
    return "oam_container"


def _find_lmt_dedicated_pod(namespace, pod_items=None):
    explicit = str(getattr(config, "LMT_POD_NAME", "") or "").strip()
    if explicit:
        return explicit

    prefix = str(getattr(config, "LMT_POD_PREFIX", "lmt-cli") or "lmt-cli").strip().lower()
    pods = _all_active_pods(namespace, pod_items=pod_items)
    for name in sorted(pods):
        if prefix in name.lower():
            return name
    raise RuntimeError(
        "Cannot find dedicated LMT pod in {} (LMT_POD_NAME='{}', LMT_POD_PREFIX='{}')".format(
            namespace,
            explicit,
            prefix,
        )
    )


def _resolve_lmt_exec_target(namespace, pod_items=None):
    mode = _resolve_lmt_target_mode(namespace)
    if mode == "dedicated_pod":
        pod = _find_lmt_dedicated_pod(namespace, pod_items=pod_items)
        container = str(getattr(config, "LMT_POD_CONTAINER", "") or "").strip()
        source = "dedicated_pod"
    else:
        pod = _find_oam_pod(namespace, pod_items=pod_items)
        container = str(config.OAM_CONTAINER or "").strip()
        source = "oam_container"
    return {"mode": mode, "pod": pod, "container": container, "source": source}


def _collect_role_state(involved_components):
    role = {}

    if "ddb" in involved_components:
        role["ddb"] = {
            "masters": ddb_discover.find_ddb_masters(),
            "slaves": ddb_discover.find_ddb_non_masters(),
        }

    if "rc" in involved_components or "etcd" in involved_components:
        rc_cluster, rc_url = rc_discover.fetch_rc_cluster()
        role["rc_source_url"] = rc_url
        if "rc" in involved_components:
            role["rc"] = {
                "leader": rc_discover.find_rc_leader(rc_cluster),
                "followers": rc_discover.find_rc_followers(rc_cluster),
            }
        if "etcd" in involved_components:
            role["etcd"] = {
                "leader": rc_discover.find_etcd_leader(rc_cluster),
                "followers": rc_discover.find_etcd_followers(rc_cluster),
            }

    return role


def _extract_balanced_json(text):
    s = text or ""
    start = -1
    stack = []
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if start < 0:
            if ch in "[{":
                start = i
                stack = [ch]
                in_str = False
                esc = False
            continue

        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
            continue
        if ch in "[{":
            stack.append(ch)
            continue
        if ch in "]}":
            if not stack:
                continue
            left = stack[-1]
            if (left == "[" and ch == "]") or (left == "{" and ch == "}"):
                stack.pop()
                if not stack:
                    return s[start : i + 1]
    return None


def _parse_lmt_output(output):
    txt = (output or "").strip()
    if not txt:
        return None

    # first pass: parse each line
    for ln in txt.splitlines():
        t = ln.strip()
        if not t:
            continue
        if t.startswith("{") or t.startswith("["):
            try:
                return json.loads(t)
            except Exception:
                pass

    # second pass: parse a balanced json block from the fragment
    blk = _extract_balanced_json(txt)
    if blk:
        try:
            return json.loads(blk)
        except Exception:
            return None
    return None


def _try_parse_json_string(val):
    if not isinstance(val, str):
        return None
    s = val.strip()
    if not s or (not s.startswith("{") and not s.startswith("[")):
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _normalize_lmt_obj(obj):
    """Recursively decode JSON-string fields for easier log reading."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            parsed = _try_parse_json_string(v)
            out[k] = _normalize_lmt_obj(parsed if parsed is not None else v)
        return out
    if isinstance(obj, list):
        return [_normalize_lmt_obj(x) for x in obj]
    return obj


def _pretty_json_lines(obj):
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
    return text.splitlines()


def _display_lmt_command(command):
    c = (command or "").strip()
    if c.startswith("lmt-cli list"):
        c = c[len("lmt-cli list") :].strip()
    # Keep execution command unchanged, but hide table format flag in logs.
    c = c.replace("--format table", "").strip()
    return " ".join(c.split())


def _render_lmt_compact(command, parsed):
    title = _display_lmt_command(command)
    if parsed is None:
        return ["{} => <parse-failed>".format(title)]

    normalized = _normalize_lmt_obj(parsed)
    lines = []

    if isinstance(normalized, dict):
        c = normalized.get("currentItemCount")
        t = normalized.get("totalItems")
        p = normalized.get("pageIndex")
        meta = []
        if c is not None:
            meta.append("currentItemCount={}".format(c))
        if t is not None:
            meta.append("totalItems={}".format(t))
        if p is not None:
            meta.append("pageIndex={}".format(p))
        lines.append("{}{}".format(title, (" => " + ", ".join(meta)) if meta else ""))

        records = normalized.get("records")
        if isinstance(records, list):
            for i, rec in enumerate(records, 1):
                lines.append("  record[{}]:".format(i))
                for ln in _pretty_json_lines(rec):
                    lines.append("    {}".format(ln))
            return lines

    if isinstance(normalized, list):
        lines.append("{} => count={}".format(title, len(normalized)))
        for i, item in enumerate(normalized, 1):
            lines.append("  item[{}]:".format(i))
            for ln in _pretty_json_lines(item):
                lines.append("    {}".format(ln))
        return lines

    lines.append("{}:".format(title))
    for ln in _pretty_json_lines(normalized):
        lines.append("  {}".format(ln))
    return lines


_LMT_TABLES = [
    "upfGetTalkerRole",
    "upfGetNodeAssociateInfo",
    "upfGetLicenseUsage",
    "upfGetSessionNum",
    "upfGetUpcSessionNum",
    "upfGetUpuInstanceStatus",
    "upfGetWholeMachineRate",
    "upfGetUpuForwardRate",
    "upfGetRoleInterfaceRate",
]


def _default_lmt_commands(lmt_mode):
    mode = (lmt_mode or "table").strip().lower()
    if mode == "json":
        return ["lmt-cli list {}".format(x) for x in _LMT_TABLES]
    return ["lmt-cli list {} --format table".format(x) for x in _LMT_TABLES]


def _normalize_lmt_commands(commands):
    out = []
    for command in (commands or []):
        text = str(command or "").strip()
        if text:
            out.append(text)
    return out


def resolve_lmt_commands(observer_cfg, phase, lmt_mode):
    cfg = observer_cfg or {}
    lmt_cfg = cfg.get("lmt") or {}
    phase_key = "{}_commands".format((phase or "").strip().lower())
    commands = _normalize_lmt_commands(lmt_cfg.get(phase_key))
    if commands:
        return commands
    commands = _normalize_lmt_commands(lmt_cfg.get("commands"))
    if commands:
        return commands
    return _default_lmt_commands(lmt_mode)


def _collect_lmt(namespace, lmt_mode="table", commands=None, pod_items=None):
    if not config.is_lmt_configured():
        return {
            "oam_pod": "",
            "lmt_pod": "",
            "rows": [],
            "raw_output": "",
            "skipped": True,
            "reason": "LMT credentials are not configured",
        }

    target = _resolve_lmt_exec_target(namespace, pod_items=pod_items)
    login_mode = "discover_in_pod" if target.get("mode") == "dedicated_pod" else "explicit"
    commands = _normalize_lmt_commands(commands) or _default_lmt_commands(lmt_mode)
    try:
        ret = run_lmt_commands_in_container(
            namespace,
            target.get("pod"),
            target.get("container"),
            config.LMT_IP,
            config.LMT_PORT,
            config.LMT_USER,
            config.LMT_PASSWORD,
            commands,
            login_mode=login_mode,
        )
    except RuntimeError as e:
        if "requires POSIX pty/fcntl support" in str(e):
            if is_remote_apply_enabled():
                ret = run_lmt_commands_in_container_remote(
                    namespace,
                    target.get("pod"),
                    target.get("container"),
                    config.LMT_IP,
                    config.LMT_PORT,
                    config.LMT_USER,
                    config.LMT_PASSWORD,
                    commands,
                    login_mode=login_mode,
                )
            else:
                return {
                    "oam_pod": target.get("pod", ""),
                    "lmt_pod": target.get("pod", ""),
                    "rows": [],
                    "raw_output": "",
                    "target_mode": target.get("mode", ""),
                    "target_source": target.get("source", ""),
                    "skipped": True,
                    "reason": "LMT PTY collection is not supported on this platform",
                }
        else:
            raise

    rows = []
    for item in ret.get("results") or []:
        raw = item.get("output", "")
        cleaned = _clean_lmt_table_output(raw, item.get("command", ""))
        if not cleaned:
            cleaned = _fallback_lmt_text(raw)
        rows.append(
            {
                "command": item.get("command"),
                "table_text": cleaned,
                "status": _lmt_row_status(cleaned),
            }
        )

    return {
        "oam_pod": target.get("pod", ""),
        "lmt_pod": target.get("pod", ""),
        "target_mode": target.get("mode", ""),
        "target_source": target.get("source", ""),
        "rows": rows,
        "raw_output": ret.get("raw_output", ""),
    }


def _parse_rfc3339(ts):
    t = (ts or "").strip()
    if not t:
        return None
    # Keep compatibility with Python 3.6 where datetime.fromisoformat is unavailable.
    if t.endswith("Z"):
        t = t[:-1]
    try:
        return datetime.strptime(t, "%Y-%m-%dT%H:%M:%S%z")
    except Exception:
        pass
    try:
        # e.g. 2026-03-04T17:43:20
        return datetime.strptime(t, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _clean_lmt_table_output(text, command):
    command_text = (command or "").strip()
    display_name = _display_lmt_command(command_text)
    out = []
    for ln in (text or "").splitlines():
        line = (ln or "").rstrip()
        s = line.strip()
        if not s:
            continue
        # remove echoed command / shell prompt / marker lines
        if command_text and (s == command_text or command_text in s):
            continue
        if "lmt-cli list" in s and "--format table" in s:
            continue
        if display_name and s.startswith(display_name):
            continue
        if "__CMD_BEGIN_" in s or "__CMD_END_" in s or "__CMD_DONE_" in s:
            continue
        if s.startswith("root@") or s.startswith("bash-"):
            continue
        if s.lower() == "echo":
            continue
        out.append(line)
    return "\n".join(out).strip()


def _fallback_lmt_text(text):
    """Best-effort fallback to avoid empty LMT blocks in logs."""
    out = []
    for ln in (text or "").splitlines():
        s = (ln or "").strip()
        if not s:
            continue
        if "__CMD_BEGIN_" in s or "__CMD_END_" in s or "__CMD_DONE_" in s:
            continue
        if "lmt-cli list" in s and "--format table" in s:
            continue
        out.append((ln or "").rstrip())
    return "\n".join(out).strip()


def _table_name(command):
    return _display_lmt_command(command)


def _lmt_row_status(text):
    body = str(text or "").strip()
    if not body:
        return "empty"
    low = body.lower()
    if "error" in low or "failed" in low or "exception" in low:
        return "error"
    return "ok"


def _log_lmt_summary(case_log, lmt_state, phase):
    rows = lmt_state.get("rows") or []
    if not rows:
        return
    case_log.log("[{}] LMT Check Summary".format(phase))
    for row in rows:
        case_log.log("  [{}] status={}".format(_table_name(row.get("command", "")), row.get("status", "unknown")))


def _iter_owner_refs(pod_items, pod_names):
    wanted = set(pod_names or [])
    out = []
    seen = set()
    for it in (pod_items or []):
        meta = it.get("metadata") or {}
        name = (meta.get("name") or "").strip()
        if name not in wanted:
            continue
        for owner in (meta.get("ownerReferences") or []):
            owner_kind = (owner.get("kind") or "").strip()
            owner_name = (owner.get("name") or "").strip()
            key = (owner_kind, owner_name)
            if owner_kind and owner_name and key not in seen:
                seen.add(key)
                out.append(key)
    return out


def _event_timestamp(event):
    series = event.get("series") or {}
    for key in ("eventTime", "lastTimestamp", "firstTimestamp"):
        value = (event.get(key) or "").strip()
        ts = _parse_rfc3339(value)
        if ts:
            return ts
    value = (series.get("lastObservedTime") or "").strip()
    ts = _parse_rfc3339(value)
    if ts:
        return ts
    meta = event.get("metadata") or {}
    value = (meta.get("creationTimestamp") or "").strip()
    return _parse_rfc3339(value)


def _event_last_seen_text(event):
    series = event.get("series") or {}
    for key in ("lastTimestamp", "eventTime", "firstTimestamp"):
        value = (event.get(key) or "").strip()
        if value:
            return value
    value = (series.get("lastObservedTime") or "").strip()
    if value:
        return value
    meta = event.get("metadata") or {}
    return (meta.get("creationTimestamp") or "").strip() or "<nil>"


def _is_runtime_event_relevant(kind, name, pods, owner_names, workflow_name):
    if name in pods:
        return True
    if name in owner_names:
        return True
    if workflow_name:
        wf = str(workflow_name).strip()
        if wf and (name == wf or wf in name):
            return True
    if kind.endswith("Chaos") and workflow_name:
        wf = str(workflow_name).strip()
        if wf and wf in name:
            return True
    return False


def _parse_describe_events_block(text, object_kind, object_name):
    lines = (text or "").splitlines()
    start = -1
    for idx, line in enumerate(lines):
        if line.startswith("Events:"):
            start = idx
            break
    if start < 0:
        return []
    first = lines[start].strip()
    if "<none>" in first:
        return []

    rows = []
    for line in lines[start + 1 :]:
        if not line.strip():
            break
        stripped = line.strip()
        if stripped.startswith("Type") or set(stripped) == {"-"}:
            continue
        parts = re.split(r"\s{2,}", stripped, maxsplit=4)
        if len(parts) < 5:
            continue
        typ, reason, age, _from, message = parts
        rows.append(
            {
                "LASTSEEN": age,
                "TYPE": typ,
                "REASON": reason,
                "OBJECT_KIND": object_kind,
                "OBJECT_NAME": object_name,
                "MESSAGE": message,
            }
        )
    return rows


def _collect_describe_events_rows(namespace, pod_names, owner_refs):
    rows = []
    for pod in sorted(set(pod_names or [])):
        text = sh("kubectl -n {} describe pod {}".format(namespace, shlex.quote(pod)), check=False)
        rows.extend(_parse_describe_events_block(text, "Pod", pod))
    for owner_kind, owner_name in (owner_refs or []):
        kind_token = "{}{}".format(owner_kind[:1].lower(), owner_kind[1:]) if owner_kind else ""
        if not kind_token or not owner_name:
            continue
        text = sh(
            "kubectl -n {} describe {}/{}".format(namespace, shlex.quote(kind_token), shlex.quote(owner_name)),
            check=False,
        )
        rows.extend(_parse_describe_events_block(text, owner_kind, owner_name))
    return rows


def _collect_target_events_rows(namespace, pod_names, workflow_name="", since_time=None, pod_items=None):
    events = json.loads(sh("kubectl get events -n {} -o json".format(namespace), check=False) or "{}")
    current_pod_items = _get_all_pods(namespace, pod_items=pod_items)
    pods = set(pod_names or [])
    owner_refs = _iter_owner_refs(current_pod_items, pods)
    owner_names = set([name for _kind, name in owner_refs])
    rows = []
    for event in (events.get("items") or []):
        involved = event.get("involvedObject") or {}
        kind = (involved.get("kind") or "").strip()
        obj_name = (involved.get("name") or "").strip()
        if not _is_runtime_event_relevant(kind, obj_name, pods, owner_names, workflow_name):
            continue
        if since_time:
            ts = _event_timestamp(event)
            if ts and ts < since_time:
                continue
        msg = " ".join(str(event.get("message") or "").split())
        rows.append(
            {
                "LASTSEEN": _event_last_seen_text(event),
                "TYPE": (event.get("type") or "").strip(),
                "REASON": (event.get("reason") or "").strip(),
                "OBJECT_KIND": kind,
                "OBJECT_NAME": obj_name,
                "MESSAGE": msg,
            }
        )
    rows.sort(key=lambda item: (item.get("LASTSEEN") == "<nil>", item.get("LASTSEEN", ""), item.get("OBJECT_KIND", ""), item.get("OBJECT_NAME", ""), item.get("REASON", "")))
    if not rows:
        rows = _collect_describe_events_rows(namespace, sorted(pods), owner_refs)
    return rows


def _log_pod_table(case_log, title, pod_status_map):
    case_log.log(title)
    case_log.log("  {:<48} {:<12} {}".format("POD", "PHASE", "NODE"))
    case_log.log("  {}".format("-" * 110))
    for pod in sorted(pod_status_map.keys()):
        row = pod_status_map[pod]
        case_log.log("  {:<48} {:<12} {}".format(pod, row.get("status", ""), row.get("node", "")))


def _log_replacements(case_log, replacements, title="[POST] Pod Replacement Mapping"):
    if not replacements:
        return
    case_log.log(title)
    for old_name in sorted(replacements.keys()):
        item = replacements[old_name]
        case_log.log(
            "  {} -> {}@{}".format(
                old_name,
                item.get("new_name", "<unknown>"),
                (item.get("row") or {}).get("node", "<unknown-node>"),
            )
        )


def _log_role_state(case_log, title, role_state):
    case_log.log(title)
    if role_state.get("ddb"):
        ddb = role_state["ddb"]
        case_log.log("  DDB masters: {}".format(", ".join(["{}({})".format(x.get("pod"), x.get("ip")) for x in ddb.get("masters", [])]) or "<none>"))
        case_log.log("  DDB slaves : {}".format(", ".join(["{}({})".format(x.get("pod"), x.get("ip")) for x in ddb.get("slaves", [])]) or "<none>"))
    if role_state.get("rc"):
        rc = role_state["rc"]
        leader = rc.get("leader") or {}
        case_log.log("  RC leader  : {}({})".format(leader.get("pod", ""), leader.get("ip", "")))
        case_log.log("  RC followers: {}".format(", ".join(["{}({})".format(x.get("pod"), x.get("ip")) for x in rc.get("followers", [])]) or "<none>"))
    if role_state.get("etcd"):
        etcd = role_state["etcd"]
        leader = etcd.get("leader") or {}
        case_log.log("  ETCD leader: {}({})".format(leader.get("pod", ""), leader.get("ip", "")))
        case_log.log("  ETCD followers: {}".format(", ".join(["{}({})".format(x.get("pod"), x.get("ip")) for x in etcd.get("followers", [])]) or "<none>"))


def _sanitize_log_text(text, max_lines):
    lines = []
    for line in (text or "").splitlines():
        clean = _ANSI_ESCAPE_RE.sub("", line.rstrip())
        clean = _CONTROL_CHAR_RE.sub("", clean)
        lines.append(clean)
    if max_lines and len(lines) > max_lines:
        lines = lines[-max_lines:]
    return lines


def _parse_log_timestamp(line):
    text = str(line or "").strip()
    if not text:
        return None
    match = _LOG_TS_RE.search(text)
    if not match:
        return None
    raw = match.group("ts").replace(",", ".")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    tz_match = re.search(r"[+-]\d{4}$", raw)
    if tz_match:
        raw = raw[:-5] + tz_match.group(0)[:3] + ":" + tz_match.group(0)[3:]
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_error_log_line(line):
    return bool(_ERROR_LOG_RE.search(str(line or "")))


def _filter_runtime_log_lines(text, max_lines, since_time=None, error_only=False, enforce_line_timestamp=False):
    lines = []
    since_utc = since_time.astimezone(timezone.utc) if since_time is not None else None
    for clean in _sanitize_log_text(text, 0):
        if not clean.strip():
            continue
        if error_only and not _is_error_log_line(clean):
            continue
        if since_utc is not None and enforce_line_timestamp:
            line_ts = _parse_log_timestamp(clean)
            if line_ts is None or line_ts < since_utc:
                continue
        lines.append(clean)
    if max_lines and len(lines) > max_lines:
        lines = lines[-max_lines:]
    return lines


def _is_pod_not_found_error(text):
    low = str(text or "").strip().lower()
    return 'error from server (notfound): pods "' in low and '" not found' in low


def _clean_ssh_noise(text):
    cleaned = _ANSI_ESCAPE_RE.sub("", (text or "").replace("\r\n", "\n").replace("\r", "\n"))
    lines = []
    for ln in cleaned.splitlines():
        s = (ln or "").strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("connection to ") and low.endswith(" closed."):
            continue
        lines.append(ln)
    return "\n".join(lines).strip()


def _run_cmd(cmd):
    p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    out = _clean_ssh_noise((p.stdout or "").strip())
    err = _clean_ssh_noise((p.stderr or "").strip())
    return p.returncode, out, err


def _ssh_host_for_node(node_name):
    mapping = getattr(config, "NET_VERIFY_NODE_HOST_MAP", {}) or {}
    return mapping.get(node_name, node_name)


def _sanitize_ssh_extra_opts(extra):
    tokens = [x for x in shlex.split(extra or "") if x]
    kept = []
    force_no_tty = True
    for tok in tokens:
        if tok in ("-t", "-tt"):
            continue
        if tok == "-T":
            force_no_tty = False
        kept.append(tok)
    if force_no_tty:
        kept.insert(0, "-T")
    return kept


def _build_ssh_cmd(host, remote_cmd):
    user = (
        getattr(config, "NET_VERIFY_REMOTE_SSH_USER", getattr(config, "NET_VERIFY_SSH_USER", "")) or ""
    ).strip()
    port = int(getattr(config, "NET_VERIFY_SSH_PORT", 22) or 22)
    extra = (getattr(config, "NET_VERIFY_SSH_EXTRA_OPTS", "") or "").strip()
    ssh_cfg = (getattr(config, "NET_VERIFY_SSH_CONFIG_FILE", "") or "").strip()
    user_host = ("{}@{}".format(user, host) if user else host)

    parts = ["ssh"]
    if ssh_cfg:
        parts.extend(["-F", shlex.quote(ssh_cfg)])
    for tok in _sanitize_ssh_extra_opts(extra):
        parts.append(shlex.quote(tok))
    parts.extend(["-p", str(port), shlex.quote(user_host), shlex.quote(remote_cmd)])
    return " ".join(parts)


def _run_on_node(node_name, remote_cmd):
    # Keep behavior aligned with network_verify execution permissions.
    local_aliases = set()
    for it in (getattr(config, "NET_VERIFY_LOCAL_NODE_NAMES", []) or []):
        if it:
            s = str(it).strip().lower()
            local_aliases.add(s)
            local_aliases.add(s.split(".")[0])

    n = str(node_name or "").strip().lower()
    is_local = n in local_aliases or n.split(".")[0] in local_aliases
    if is_local:
        local_user = (getattr(config, "NET_VERIFY_LOCAL_EXEC_USER", "") or "").strip()
        cur_user = (getpass.getuser() or "").strip()
        if local_user and local_user != cur_user:
            cmd = "sudo -n -u {} sh -lc {}".format(shlex.quote(local_user), shlex.quote(remote_cmd))
            return _run_cmd(cmd)
        return _run_cmd(remote_cmd)

    host = _ssh_host_for_node(node_name)
    return _run_cmd(_build_ssh_cmd(host, remote_cmd))


def _uses_internal_smf_logs(namespace, pod):
    ns = str(namespace or "").strip().lower()
    return ns.startswith("ns-smf")


def _uses_dupf_cross_node_host_logs(namespace):
    ns = str(namespace or "").strip().lower()
    return ns.startswith("ns-dupf")


def _uses_dupf_internal_logs(namespace, pod):
    ns = str(namespace or "").strip().lower()
    if not ns.startswith("ns-dupf"):
        return False
    component = _component_of_pod(pod)
    return component in ("upc", "rc", "sdb", "etcd")


def _get_target_log_pods(pre_state, common_state):
    pods = []
    seen = set()

    for pod in (pre_state.get("target_pods") or []):
        if pod and pod not in seen:
            pods.append(pod)
            seen.add(pod)

    for item in (common_state.get("replacements") or {}).values():
        new_name = item.get("new_name")
        if new_name and new_name not in seen:
            pods.append(new_name)
            seen.add(new_name)

    for pod in (pre_state.get("role_source_pods") or []):
        if pod and pod not in seen:
            pods.append(pod)
            seen.add(pod)

    return pods


def _collect_internal_smf_logs(namespace, pod):
    cmd = (
        "if [ ! -d {d} ]; then exit 0; fi; "
        "for f in $(find {d} -type f 2>/dev/null | xargs -r ls -1t 2>/dev/null | head -n {file_count}); do "
        "echo '>>>FILE:'$f; "
        "tail -n {tail_lines} \"$f\" 2>/dev/null; "
        "done"
    ).format(
        d=_SMF_INTERNAL_LOG_DIR,
        file_count=_SMF_INTERNAL_LOG_FILES,
        tail_lines=_SMF_INTERNAL_LOG_TAIL_LINES,
    )
    try:
        return exec_in_pod(namespace, pod, cmd)
    except Exception as e:
        if _is_pod_not_found_error(e):
            return ""
        return "<internal-log-read-failed: {}>".format(e)


def _collect_internal_dupf_logs(namespace, pod):
    pod_text = shlex.quote(str(pod or "").strip())
    cmd = (
        "if [ ! -d {d} ]; then exit 0; fi; "
        "for f in $(find {d} -type f 2>/dev/null | grep -F {pod} | xargs -r ls -1t 2>/dev/null | head -n {file_count}); do "
        "echo '>>>FILE:'$f; "
        "tail -n {tail_lines} \"$f\" 2>/dev/null; "
        "done"
    ).format(
        d=_DUPF_INTERNAL_LOG_DIR,
        pod=pod_text,
        file_count=_DUPF_INTERNAL_LOG_FILES,
        tail_lines=_DUPF_INTERNAL_LOG_TAIL_LINES,
    )
    try:
        return exec_in_pod(namespace, pod, cmd)
    except Exception as e:
        if _is_pod_not_found_error(e):
            return ""
        return "<internal-log-read-failed: {}>".format(e)


def _collect_single_pod_runtime_log(namespace, pod, since_time=None, pod_status_map=None):
    source = "kubectl_logs"
    node = ((pod_status_map or {}).get(pod) or {}).get("node", "").strip()
    strict_since_filter = since_time is not None
    since_time_arg = ""
    if since_time:
        since_time_arg = " --since-time={}".format(since_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    if _uses_internal_smf_logs(namespace, pod):
        if strict_since_filter:
            source = "pod_internal:/var/log/service-logs/smf(skipped:strict-since-time)"
            text = ""
        else:
            source = "pod_internal:/var/log/service-logs/smf"
            text = _collect_internal_smf_logs(namespace, pod)
    else:
        cmd = "kubectl -n {} logs {} --all-containers=true --tail={}{}".format(
            namespace, pod, _POST_LOG_TAIL_LINES
            , since_time_arg
        )
        text = sh(cmd, check=False)
        lines = _filter_runtime_log_lines(
            text,
            _POST_LOG_MAX_LINES_PER_POD,
            since_time=since_time,
            error_only=True,
            enforce_line_timestamp=False,
        )
        if not lines:
            prev_cmd = "kubectl -n {} logs {} --all-containers=true --previous --tail={}{}".format(
                namespace, pod, _POST_LOG_TAIL_LINES, since_time_arg
            )
            prev_text = sh(prev_cmd, check=False)
            prev_lines = _filter_runtime_log_lines(
                prev_text,
                _POST_LOG_MAX_LINES_PER_POD,
                since_time=since_time,
                error_only=True,
                enforce_line_timestamp=False,
            )
            if prev_lines:
                text = prev_text
                source = "kubectl_logs_previous"
        if (
            not _filter_runtime_log_lines(
                text,
                _POST_LOG_MAX_LINES_PER_POD,
                since_time=since_time,
                error_only=True,
                enforce_line_timestamp=False,
            )
            and not strict_since_filter
            and _uses_dupf_internal_logs(namespace, pod)
        ):
            internal_text = _collect_internal_dupf_logs(namespace, pod)
            internal_lines = _filter_runtime_log_lines(
                internal_text,
                _POST_LOG_MAX_LINES_PER_POD * 2,
                since_time=since_time,
                error_only=True,
                enforce_line_timestamp=True,
            )
            if internal_lines:
                text = internal_text
                source = "pod_internal:/var/log/service-logs"
    lines = _filter_runtime_log_lines(
        text,
        _POST_LOG_MAX_LINES_PER_POD,
        since_time=since_time,
        error_only=True,
        enforce_line_timestamp=False,
    )
    return {
        "pod": pod,
        "node": node,
        "component": _component_of_pod(pod),
        "source": source,
        "lines": lines,
        "empty": not bool(lines),
    }


def _resolve_observer_log_workers(pod_count):
    pod_count = max(0, int(pod_count or 0))
    if pod_count <= 0:
        return 0
    cfg_val = int(getattr(config, "OBSERVER_LOG_MAX_WORKERS", 0) or 0)
    if cfg_val > 0:
        return max(1, min(pod_count, cfg_val))
    return max(1, min(pod_count, 4))


def _collect_pod_runtime_logs(namespace, pod_names, since_time=None, pod_items=None):
    if not bool(getattr(config, "OBSERVER_RUNTIME_LOGS_ENABLED", True)):
        return []

    pod_list = list(pod_names or [])
    if not pod_list:
        return []

    pod_status_map = _get_pod_status_map(namespace, pod_list, pod_items=pod_items)
    out = [None] * len(pod_list)
    workers = _resolve_observer_log_workers(len(pod_list))

    if workers <= 1:
        for idx, pod in enumerate(pod_list):
            out[idx] = _collect_single_pod_runtime_log(
                namespace,
                pod,
                since_time=since_time,
                pod_status_map=pod_status_map,
            )
        return out

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = []
        for idx, pod in enumerate(pod_list):
            futures.append(
                (
                    idx,
                    pool.submit(
                        _collect_single_pod_runtime_log,
                        namespace,
                        pod,
                        since_time,
                        pod_status_map,
                    ),
                )
            )
        for idx, future in futures:
            out[idx] = future.result()
    return out


def _collect_dupf_mq_host_logs(namespace, pod_names, since_time=None, pod_items=None):
    # Find candidate nodes from mq pods first; fallback to target pod nodes.
    data = {"items": _get_all_pods(namespace, pod_items=pod_items)}
    target_set = set(pod_names or [])
    mq_nodes = set()
    fallback_nodes = set()
    for it in data.get("items", []):
        name = ((it.get("metadata") or {}).get("name") or "").strip()
        node = ((it.get("spec") or {}).get("nodeName") or "").strip()
        if not name or not node:
            continue
        if "mq" in name.lower():
            mq_nodes.add(node)
        if name in target_set:
            fallback_nodes.add(node)

    nodes = sorted(mq_nodes or fallback_nodes)
    out = []
    if not nodes:
        return out

    cmd = (
        "if [ ! -d {d} ]; then echo '__NO_DIR__'; exit 0; fi; "
        "for f in $(find {d} -type f 2>/dev/null | xargs -r ls -1t 2>/dev/null | head -n {file_count}); do "
        "echo '>>>FILE:'$f; "
        "tail -n {tail_lines} \"$f\" 2>/dev/null; "
        "done"
    ).format(
        d=_DUPF_MQ_HOST_LOG_DIR,
        file_count=_DUPF_MQ_HOST_LOG_FILES,
        tail_lines=_DUPF_MQ_HOST_LOG_TAIL_LINES,
    )

    for node in nodes:
        rc, stdout, stderr = _run_on_node(node, cmd)
        text = stdout
        if stderr:
            text = "{}\n{}".format(stdout, stderr).strip()
        lines = _filter_runtime_log_lines(
            text,
            _POST_LOG_MAX_LINES_PER_POD * 2,
            since_time=since_time,
            error_only=True,
            enforce_line_timestamp=True,
        )
        if rc != 0 and not lines:
            lines = ["<host-log-read-failed rc={}>".format(rc)]
        out.append(
            {
                "pod": "",
                "node": node,
                "component": "mq",
                "source": "node_fs:{}".format(_DUPF_MQ_HOST_LOG_DIR),
                "lines": lines,
                "empty": not bool(lines),
            }
        )
    return out


def _should_collect_dupf_mq_logs(pre_state, common_state):
    components = set(pre_state.get("involved_components") or [])
    if "mq" in components:
        return True
    for pod in _get_target_log_pods(pre_state, common_state):
        if _component_of_pod(pod) == "mq":
            return True
    return False


def _log_runtime_target_logs(case_log, runtime_logs):
    case_log.log("[POST] Runtime Target ERROR Logs")
    if not runtime_logs:
        case_log.log("  <none>")
        return

    for item in runtime_logs:
        node_part = ""
        if item.get("node"):
            node_part = " node={}".format(item.get("node"))
        case_log.log(
            "  [pod={}]{} component={} source={}".format(
                item.get("pod", ""),
                node_part,
                item.get("component", ""),
                item.get("source", ""),
            )
        )
        lines = item.get("lines") or []
        if not lines:
            case_log.log("    <empty; no ERROR logs in time window>")
            continue
        for line in lines:
            case_log.log("    {}".format(line))


def _log_lmt_snapshot(case_log, lmt_state, phase):
    if lmt_state.get("skipped"):
        case_log.log("[{}] LMT Business Snapshot skipped: {}".format(phase, lmt_state.get("reason", "unknown")))
        return
    _log_lmt_summary(case_log, lmt_state, phase)
    case_log.log(
        "[{}] LMT Business Snapshot (pod={}, mode={})".format(
            phase,
            lmt_state.get("lmt_pod", lmt_state.get("oam_pod", "")),
            lmt_state.get("target_mode", ""),
        )
    )
    for row in (lmt_state.get("rows") or []):
        title = _table_name(row.get("command", ""))
        table = row.get("table_text", "")
        case_log.log("  [{}]".format(title))
        case_log.log("{}".format(table if table else "    <empty; raw=/tmp/lmt_raw_pty_multi.txt>"))



def _collect_pre_common_state(namespace, podchaos_target_pods, role_source_pods):
    run_start = datetime.now(timezone.utc)
    involved_components = sorted({_component_of_pod(p) for p in (role_source_pods or []) if _component_of_pod(p) != "other"})
    pod_items = _get_all_pod_items(namespace)

    pod_status = _get_pod_status_map(namespace, podchaos_target_pods, pod_items=pod_items)
    role_state = _collect_role_state(involved_components)
    return {
        "target_pods": podchaos_target_pods,
        "role_source_pods": role_source_pods,
        "pre_pod_status": pod_status,
        "involved_components": involved_components,
        "run_start": run_start,
        "role_state": role_state,
        "workflow_name": "",
        "pod_items": pod_items,
    }


def _collect_post_common_state(namespace, pre_state):
    target_pods = pre_state.get("target_pods") or []
    involved_components = pre_state.get("involved_components") or []
    pod_items = _get_all_pod_items(namespace)

    pod_status = _get_pod_status_map(namespace, target_pods, pod_items=pod_items)
    role_state = _collect_role_state(involved_components)

    post_all_map = _build_pod_status_map(pod_items)
    replacements = _build_replacement_map(pre_state.get("pre_pod_status") or {}, pod_status, post_all_map)
    post_display_map = dict(pod_status)
    for old_name, item in replacements.items():
        new_name = item.get("new_name")
        row = item.get("row") or {}
        if new_name and new_name not in post_display_map:
            post_display_map[new_name] = {"status": row.get("status"), "node": row.get("node")}

    event_pods = set(target_pods)
    for item in replacements.values():
        if item.get("new_name"):
            event_pods.add(item.get("new_name"))
    runtime_events = _collect_target_events_rows(
        namespace,
        sorted(event_pods | set(pre_state.get("role_source_pods") or [])),
        workflow_name=pre_state.get("workflow_name", ""),
        since_time=pre_state.get("run_start"),
        pod_items=pod_items,
    )
    runtime_logs = _collect_pod_runtime_logs(
        namespace,
        _get_target_log_pods(pre_state, {"replacements": replacements}),
        since_time=pre_state.get("run_start"),
        pod_items=pod_items,
    )
    if _uses_dupf_cross_node_host_logs(namespace) and _should_collect_dupf_mq_logs(pre_state, {"replacements": replacements}):
        runtime_logs.extend(
            _collect_dupf_mq_host_logs(
                namespace,
                _get_target_log_pods(pre_state, {"replacements": replacements}),
                since_time=pre_state.get("run_start"),
                pod_items=pod_items,
            )
        )

    return {
        "target_pods": target_pods,
        "pod_status": pod_status,
        "role_state": role_state,
        "post_display_map": post_display_map,
        "replacements": replacements,
        "runtime_events": runtime_events,
        "runtime_logs": runtime_logs,
        "pod_items": pod_items,
    }
def collect_pre_case_state(namespace, podchaos_target_pods, role_source_pods, case_log, lmt_mode="table", common_state=None, lmt_commands=None):
    common = common_state or _collect_pre_common_state(namespace, podchaos_target_pods, role_source_pods)
    lmt_state = _collect_lmt(namespace, lmt_mode=lmt_mode, commands=lmt_commands, pod_items=common.get("pod_items"))

    if not (common.get("target_pods") or []):
        case_log.log("[PRE] podchaos selected pods is empty")
    case_log.log("[PRE] podchaos selected pods count={} components={}".format(len(common.get("target_pods") or []), common.get("involved_components") or []))
    _log_pod_table(case_log, "[PRE] Pod Status", common.get("pre_pod_status") or {})
    _log_role_state(case_log, "[PRE] Component Overall Role State", common.get("role_state") or {})
    _log_lmt_snapshot(case_log, lmt_state, "PRE")

    out = dict(common)
    out["pre_lmt_state"] = lmt_state
    return out


def collect_post_case_state(namespace, pre_state, case_log, lmt_mode="table", common_state=None, lmt_commands=None):
    common = common_state or _collect_post_common_state(namespace, pre_state)
    lmt_state = _collect_lmt(namespace, lmt_mode=lmt_mode, commands=lmt_commands, pod_items=common.get("pod_items"))

    _log_pod_table(case_log, "[POST] Pod Status", common.get("post_display_map") or {})
    _log_replacements(case_log, common.get("replacements") or {})

    case_log.log("[COMPARE] Pod PRE -> POST")
    case_log.log("  {:<48} {:<35} {:<35}".format("POD", "PRE(phase@node)", "POST(phase@node)"))
    case_log.log("  {}".format("-" * 130))
    pre_map = pre_state.get("pre_pod_status") or {}
    pod_status = common.get("pod_status") or {}
    replacements = common.get("replacements") or {}
    all_pods = sorted(set(pre_map.keys()) | set(pod_status.keys()))
    for pod in all_pods:
        pr = pre_map.get(pod) or {}
        po = pod_status.get(pod) or {}
        note = ""
        if not po and pod in replacements:
            repl = replacements[pod]
            po = {"status": (repl.get("row") or {}).get("status"), "node": (repl.get("row") or {}).get("node")}
            note = "  -> replaced_by={}".format(repl.get("new_name"))
        pre_txt = _fmt_phase_node(pr)
        post_txt = _fmt_phase_node(po)
        case_log.log("  {:<48} {:<35} {:<35}{}".format(pod, pre_txt, post_txt, note))

    _log_role_state(case_log, "[POST] Component Overall Role State", common.get("role_state") or {})
    _log_lmt_snapshot(case_log, lmt_state, "POST")

    case_log.log("[POST] Runtime Target Events")
    case_log.log("  LASTSEEN TYPE REASON OBJECT_KIND OBJECT_NAME MESSAGE")
    for r in (common.get("runtime_events") or []):
        case_log.log("  {LASTSEEN} {TYPE} {REASON} {OBJECT_KIND} {OBJECT_NAME} {MESSAGE}".format(**r))
    _log_runtime_target_logs(case_log, common.get("runtime_logs") or [])


def _fmt_phase_node(row):
    if not row:
        return "<missing>"
    phase = (row.get("status") or "<unknown-phase>").strip()
    node = (row.get("node") or "<unknown-node>").strip()
    return "{}@{}".format(phase, node)


def _stable_pod_key(name):
    p = (name or "").strip().split("-")
    # Deployment/ReplicaSet pods usually end with '-<hash>-<suffix>'
    if len(p) >= 3:
        tail1 = p[-1]
        tail2 = p[-2]
        if tail1.isalnum() and tail2.isalnum() and len(tail1) >= 4 and len(tail2) >= 6:
            return "-".join(p[:-2])
    return (name or "").strip()


def _find_replacement_pod(pre_pod, post_all_map, used_new_pods):
    key = _stable_pod_key(pre_pod)
    cands = []
    for name, row in post_all_map.items():
        if name in used_new_pods:
            continue
        if _stable_pod_key(name) != key:
            continue
        cands.append((name, row))
    if not cands:
        return None
    # Prefer running pod
    cands.sort(key=lambda x: (0 if (x[1].get("status") == "Running") else 1, x[0]))
    return cands[0]


def _build_replacement_map(pre_map, post_target_map, post_all_map):
    used_new = set()
    out = {}
    for pod in sorted(pre_map.keys()):
        if pod in post_target_map:
            continue
        repl = _find_replacement_pod(pod, post_all_map, used_new)
        if not repl:
            continue
        new_name, new_row = repl
        used_new.add(new_name)
        out[pod] = {"new_name": new_name, "row": new_row}
    return out



