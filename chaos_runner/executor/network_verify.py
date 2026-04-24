# -*- coding: utf-8 -*-
import getpass
import json
import re
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from chaos_runner import yaml_compat as yaml

from chaos_runner import config
from chaos_runner.tools.k8s import sh

_LOCAL_NODE_ALIASES = None
_NODE_HOST_MAP = None
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_NETEM_DELAY_RE = re.compile(r"\bdelay\s+([0-9.]+(?:ms|s))(?:\s+([0-9.]+(?:ms|s)))?", re.IGNORECASE)
_NETEM_LOSS_RE = re.compile(r"\bloss\s+([0-9.]+)%", re.IGNORECASE)


def _log(loggers, msg):
    for lg in (loggers or []):
        try:
            lg.log(msg)
        except Exception:
            pass


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


def _run_cmd(cmd, input_text=None):
    use_shell = isinstance(cmd, str)
    p = subprocess.run(
        cmd,
        shell=use_shell,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = _clean_ssh_noise((p.stdout or "").strip())
    err = _clean_ssh_noise((p.stderr or "").strip())
    return p.returncode, out, err


def _to_ms(val):
    if val is None:
        return None
    s = str(val).strip().lower()
    if not s:
        return None
    try:
        if s.endswith("ms"):
            return float(s[:-2])
        if s.endswith("s"):
            return float(s[:-1]) * 1000.0
        return float(s)
    except Exception:
        return None


def _to_float(val):
    if val is None:
        return None
    s = str(val).strip().lower().replace("%", "")
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _extract_network_templates(wf_yaml_text, namespace):
    doc = yaml.safe_load(wf_yaml_text) or {}
    templates = ((doc.get("spec") or {}).get("templates") or [])
    out = []

    for tpl in templates:
        if (tpl or {}).get("templateType") != "NetworkChaos":
            continue
        nc = (tpl.get("networkChaos") or {})
        action = (nc.get("action") or "").strip().lower()
        if action not in ("delay", "loss", "partition"):
            continue

        selector = nc.get("selector") or {}
        target_selector = ((nc.get("target") or {}).get("selector") or {})
        from_pods = (((selector.get("pods") or {}).get(namespace)) or [])
        to_pods = (((target_selector.get("pods") or {}).get(namespace)) or [])

        out.append(
            {
                "name": (tpl.get("name") or "").strip(),
                "action": action,
                "direction": (nc.get("direction") or "both").strip().lower(),
                "from_pods": [x for x in from_pods if isinstance(x, str) and x],
                "to_pods": [x for x in to_pods if isinstance(x, str) and x],
                "expected_loss": _to_float(((nc.get("loss") or {}).get("loss"))),
                "expected_delay_ms": _to_ms(((nc.get("delay") or {}).get("latency"))),
                "expected_jitter_ms": _to_ms(((nc.get("delay") or {}).get("jitter"))),
            }
        )
    return out


def _get_pod_meta_map(namespace, pod_names):
    data = json.loads(sh("kubectl -n {} get pod -o json".format(namespace)))
    node_host_map = _node_host_map()
    wanted = set(pod_names)
    out = {}
    for it in data.get("items", []):
        name = ((it.get("metadata") or {}).get("name") or "").strip()
        if name not in wanted:
            continue
        node = ((it.get("spec") or {}).get("nodeName") or "").strip()
        ip = ((it.get("status") or {}).get("podIP") or "").strip()
        out[name] = {"node": node, "ip": ip, "node_host": node_host_map.get(node, node)}
    return out


def _node_host_map():
    global _NODE_HOST_MAP
    if _NODE_HOST_MAP is not None:
        return _NODE_HOST_MAP

    configured = getattr(config, "NET_VERIFY_NODE_HOST_MAP", {}) or {}
    host_map = {}
    for key, value in configured.items():
        k = str(key or "").strip()
        v = str(value or "").strip()
        if k and v:
            host_map[k] = v

    data = json.loads(sh("kubectl get node -o json", check=False) or "{}")
    for it in (data.get("items") or []):
        name = ((it.get("metadata") or {}).get("name") or "").strip()
        if not name or name in host_map:
            continue
        addresses = ((it.get("status") or {}).get("addresses") or [])
        internal_ip = ""
        hostname = ""
        for item in addresses:
            addr_type = str(item.get("type") or "").strip()
            addr_value = str(item.get("address") or "").strip()
            if not addr_value:
                continue
            if addr_type == "InternalIP" and not internal_ip:
                internal_ip = addr_value
            elif addr_type == "Hostname" and not hostname:
                hostname = addr_value
        host_map[name] = internal_ip or hostname or name

    _NODE_HOST_MAP = host_map
    return _NODE_HOST_MAP


def _local_node_aliases():
    global _LOCAL_NODE_ALIASES
    if _LOCAL_NODE_ALIASES is not None:
        return _LOCAL_NODE_ALIASES

    aliases = set()
    for it in (getattr(config, "NET_VERIFY_LOCAL_NODE_NAMES", []) or []):
        if it:
            s = str(it).strip().lower()
            aliases.add(s)
            aliases.add(s.split(".")[0])

    for cmd in ("hostname", "hostname -f"):
        rc, out, _ = _run_cmd(cmd)
        if rc != 0 or not out:
            continue
        s = out.strip().lower()
        aliases.add(s)
        aliases.add(s.split(".")[0])

    _LOCAL_NODE_ALIASES = aliases
    return _LOCAL_NODE_ALIASES


def _is_local_node(node_name):
    n = (node_name or "").strip().lower()
    if not n:
        return False
    aliases = _local_node_aliases()
    return (n in aliases) or (n.split(".")[0] in aliases)


def _ssh_host_for_node(node_name):
    return _node_host_map().get(node_name, node_name)


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
    # Prefer NET_VERIFY_REMOTE_SSH_USER; keep NET_VERIFY_SSH_USER as backward-compat fallback.
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


def _build_ssh_argv(host, remote_cmd):
    user = (
        getattr(config, "NET_VERIFY_REMOTE_SSH_USER", getattr(config, "NET_VERIFY_SSH_USER", "")) or ""
    ).strip()
    port = int(getattr(config, "NET_VERIFY_SSH_PORT", 22) or 22)
    extra = (getattr(config, "NET_VERIFY_SSH_EXTRA_OPTS", "") or "").strip()
    ssh_cfg = (getattr(config, "NET_VERIFY_SSH_CONFIG_FILE", "") or "").strip()
    user_host = ("{}@{}".format(user, host) if user else host)

    parts = ["ssh"]
    if ssh_cfg:
        parts.extend(["-F", ssh_cfg])
    parts.extend(_sanitize_ssh_extra_opts(extra))
    parts.extend(["-p", str(port), user_host, remote_cmd])
    return parts


def _sudo_password(is_local):
    if is_local:
        return (getattr(config, "NET_VERIFY_LOCAL_SUDO_PASSWORD", "") or "").strip()
    return (getattr(config, "NET_VERIFY_REMOTE_SUDO_PASSWORD", "") or "").strip()


def _build_sudo_sh_command(command, run_as_user=""):
    target = str(run_as_user or "").strip()
    if target:
        return "sudo {mode} -u {user} sh -lc {cmd}"
    return "sudo {mode} sh -lc {cmd}"


def _run_sudo_on_node(node_name, command, run_as_user=""):
    is_local = _is_local_node(node_name)
    password = _sudo_password(is_local)
    mode = "-S -p ''" if password else "-n"
    target_user = str(run_as_user or "").strip()
    cmd = _build_sudo_sh_command(command, run_as_user=target_user).format(
        mode=mode,
        user=shlex.quote(target_user),
        cmd=shlex.quote(command),
    )
    input_text = (password + "\n") if password else None

    if is_local:
        return _run_cmd(cmd, input_text=input_text)

    host = _ssh_host_for_node(node_name)
    return _run_cmd(_build_ssh_argv(host, cmd), input_text=input_text)


def _run_on_node(node_name, remote_cmd):
    if _is_local_node(node_name):
        local_user = (getattr(config, "NET_VERIFY_LOCAL_EXEC_USER", "") or "").strip()
        cur_user = (getpass.getuser() or "").strip()
        if local_user and local_user != cur_user:
            return _run_sudo_on_node(node_name, remote_cmd, run_as_user=local_user)
        return _run_cmd(remote_cmd)
    host = _ssh_host_for_node(node_name)
    return _run_cmd(_build_ssh_argv(host, remote_cmd))


def _resolve_pod_pid_on_node(node_name, namespace, pod_name):
    ns = (namespace or "").strip()
    pod = (pod_name or "").strip()
    ns_q = shlex.quote(ns)
    pod_q = shlex.quote(pod)

    remote = (
        "cid=$(docker ps -q "
        " --filter label=io.kubernetes.pod.namespace={ns}"
        " --filter label=io.kubernetes.pod.name={pod}"
        " --filter label=io.kubernetes.container.name=POD | head -n 1); "
        "if [ -z \"$cid\" ]; then "
        "  cid=$(docker ps -q "
        "   --filter label=io.kubernetes.pod.namespace={ns}"
        "   --filter label=io.kubernetes.pod.name={pod} | head -n 1); "
        "fi; "
        "if [ -z \"$cid\" ]; then "
        "  echo '__PID_NOT_FOUND__'; "
        "  docker ps --format '{{{{.ID}}}} {{{{.Names}}}}' | grep -F \"_{pod_raw}_\" || true; "
        "  exit 1; "
        "fi; "
        "docker inspect -f '{{{{.State.Pid}}}}' \"$cid\""
    ).format(ns=ns_q, pod=pod_q, pod_raw=pod)

    rc, out, err = _run_sudo_on_node(node_name, remote)
    if rc != 0 or not out:
        return None, "pid-resolve-failed rc={} out={} err={}".format(rc, out, err)

    m = re.search(r"(\d+)", out)
    if not m:
        return None, "pid-not-found in docker inspect output: {}".format(out)
    pid = int(m.group(1))
    if pid <= 0:
        return None, "invalid-pid from docker inspect: {}".format(pid)
    return pid, ""


def _get_qdisc_on_pid(node_name, pid):
    remote = "nsenter -t {} -n tc qdisc show dev eth0".format(int(pid))
    return _run_sudo_on_node(node_name, remote)


def _normalize_qdisc_text(text):
    lines = []
    for ln in (text or "").splitlines():
        s = (ln or "").strip()
        if not s:
            continue
        lines.append(s)
    return "\n".join(lines)


def _extract_netem_metrics(text):
    metrics = {}
    for line in (text or "").splitlines():
        clean = (line or "").strip()
        if not clean or " netem " not in (" " + clean.lower() + " "):
            continue
        delay_match = _NETEM_DELAY_RE.search(clean)
        if delay_match:
            metrics["delay_ms"] = _to_ms(delay_match.group(1))
            if delay_match.group(2):
                metrics["jitter_ms"] = _to_ms(delay_match.group(2))
        loss_match = _NETEM_LOSS_RE.search(clean)
        if loss_match:
            metrics["loss_pct"] = _to_float(loss_match.group(1))
        metrics["line"] = clean
        break
    return metrics


def _format_netem_metrics(metrics):
    if not metrics:
        return "<none>"
    parts = []
    if metrics.get("delay_ms") is not None:
        parts.append("delay={:.3f}ms".format(float(metrics.get("delay_ms"))))
    if metrics.get("jitter_ms") is not None:
        parts.append("jitter={:.3f}ms".format(float(metrics.get("jitter_ms"))))
    if metrics.get("loss_pct") is not None:
        parts.append("loss={:.3f}%".format(float(metrics.get("loss_pct"))))
    return " ".join(parts) if parts else "<none>"


def _resolve_verify_workers(probe_count):
    probe_count = max(0, int(probe_count or 0))
    if probe_count <= 0:
        return 0
    cfg_val = int(getattr(config, "NET_VERIFY_MAX_WORKERS", 0) or 0)
    if cfg_val > 0:
        return max(1, min(probe_count, cfg_val))
    return max(1, min(probe_count, 8))


def _format_log_block(title, text):
    cleaned = _clean_ssh_noise(text)
    rows = [title]
    if not cleaned:
        rows.append("  <empty>")
        return rows
    for ln in cleaned.splitlines():
        rows.append("  {}".format((ln or "").strip()))
    return rows


def _template_metric_key(action):
    action = str(action or "").strip().lower()
    if action == "delay":
        return "delay_ms"
    if action == "loss":
        return "loss_pct"
    return ""


def _resolve_pod_runtime_state_once(pod, namespace, pod_meta):
    meta = pod_meta.get(pod) or {}
    node = meta.get("node") or ""
    if not node:
        return {
            "pod": pod,
            "node": "",
            "pid": None,
            "mode": "",
            "error": "node-missing",
            "metrics": None,
            "tc_error": "",
            "tc_failed": True,
        }

    pid, err = _resolve_pod_pid_on_node(node, namespace, pod)
    if not pid:
        return {
            "pod": pod,
            "node": node,
            "pid": None,
            "mode": "local" if _is_local_node(node) else "ssh",
            "error": err,
            "metrics": None,
            "tc_error": "",
            "tc_failed": True,
        }

    return {
        "pod": pod,
        "node": node,
        "pid": int(pid),
        "mode": "local" if _is_local_node(node) else "ssh",
        "error": "",
        "metrics": None,
        "tc_error": "",
        "tc_failed": False,
    }


def _sample_pod_netem_once(namespace, state):
    pod = state.get("pod")
    node = state.get("node")
    pid = state.get("pid")
    if not pod or not node or not pid:
        return state

    rc, qdisc_out, qdisc_err = _get_qdisc_on_pid(node, pid)
    if rc != 0:
        new_pid, _ = _resolve_pod_pid_on_node(node, namespace, pod)
        if new_pid and int(new_pid) != int(pid):
            pid = int(new_pid)
            state["pid"] = pid
            rc, qdisc_out, qdisc_err = _get_qdisc_on_pid(node, pid)

    state["metrics"] = _extract_netem_metrics(_normalize_qdisc_text(qdisc_out))
    state["tc_error"] = qdisc_err
    state["tc_failed"] = rc != 0
    return state


def _collect_pod_runtime_states(namespace, pod_names, pod_meta, workers):
    pod_list = list(pod_names or [])
    if not pod_list:
        return {}

    out = {}
    resolve_workers = max(1, min(len(pod_list), workers or 1))
    if resolve_workers > 1:
        with ThreadPoolExecutor(max_workers=resolve_workers) as pool:
            futures = [pool.submit(_resolve_pod_runtime_state_once, pod, namespace, pod_meta) for pod in pod_list]
            for pod, fut in zip(pod_list, futures):
                out[pod] = fut.result()
    else:
        for pod in pod_list:
            out[pod] = _resolve_pod_runtime_state_once(pod, namespace, pod_meta)

    sample_targets = [pod for pod in pod_list if (out.get(pod) or {}).get("pid")]
    if sample_targets:
        sample_workers = max(1, min(len(sample_targets), workers or 1))
        if sample_workers > 1:
            with ThreadPoolExecutor(max_workers=sample_workers) as pool:
                futures = [pool.submit(_sample_pod_netem_once, namespace, out[pod]) for pod in sample_targets]
                for pod, fut in zip(sample_targets, futures):
                    out[pod] = fut.result()
        else:
            for pod in sample_targets:
                out[pod] = _sample_pod_netem_once(namespace, out[pod])

    return out


def _network_templates_satisfied(nets, pod_states):
    checks = []
    for net in (nets or []):
        metric_key = _template_metric_key(net.get("action"))
        observed = []
        for pod in sorted(set((net.get("from_pods") or []) + (net.get("to_pods") or []))):
            metrics = ((pod_states.get(pod) or {}).get("metrics") or {})
            if metric_key and metrics.get(metric_key) is not None:
                observed.append(pod)
        checks.append(
            {
                "template": net.get("name", ""),
                "action": net.get("action", ""),
                "observed_pods": observed,
                "ok": bool(observed),
            }
        )
    return checks


def verify_network_chaos_before_kill(namespace, wf_yaml_text, timeout_seconds, loggers=None):
    timeout_seconds = int(timeout_seconds or 0)
    if timeout_seconds <= 0:
        timeout_seconds = int(getattr(config, "NET_VERIFY_TIMEOUT_SECONDS", 20) or 20)

    if not bool(getattr(config, "NET_VERIFY_ENABLED", True)):
        return {"verdict": "SKIP", "reason": "disabled"}

    nets = _extract_network_templates(wf_yaml_text, namespace)
    if not nets:
        return {"verdict": "SKIP", "reason": "no-network-chaos"}

    start_delay = max(0, int(getattr(config, "NET_VERIFY_START_DELAY", 3) or 3))
    poll_interval = max(1, int(getattr(config, "NET_VERIFY_POLL_INTERVAL_SECONDS", 2) or 2))
    pod_names = sorted(set([p for n in nets for p in (n.get("from_pods") or []) + (n.get("to_pods") or [])]))
    pod_meta = _get_pod_meta_map(namespace, pod_names)
    verify_workers = _resolve_verify_workers(len(pod_names))

    _log(
        loggers,
        "[NET-VERIFY] begin: templates={} pods={} timeout={}s workers={} mode=pre-kill-tc-only".format(
            len(nets), len(pod_names), timeout_seconds, verify_workers
        ),
    )
    for n in nets:
        _log(
            loggers,
            "[NET-VERIFY] template={} action={} direction={} from={} to={} exp(loss={},delay_ms={},jitter_ms={})".format(
                n.get("name", ""),
                n.get("action", ""),
                n.get("direction", ""),
                n.get("from_pods", []),
                n.get("to_pods", []),
                n.get("expected_loss"),
                n.get("expected_delay_ms"),
                n.get("expected_jitter_ms"),
            ),
        )

    end_ts = time.time() + timeout_seconds
    if start_delay > 0:
        time.sleep(min(start_delay, max(0.0, end_ts - time.time())))

    attempts = 0
    last_states = {}
    last_checks = []
    while True:
        attempts += 1
        last_states = _collect_pod_runtime_states(namespace, pod_names, pod_meta, verify_workers)
        for pod in pod_names:
            state = last_states.get(pod) or {}
            node = state.get("node") or ""
            if state.get("error") == "node-missing":
                _log(loggers, "[NET-VERIFY] pod={} node-missing, skip".format(pod))
                continue
            if state.get("error"):
                _log(loggers, "[NET-VERIFY] pod={} node={} {}".format(pod, node, state.get("error")))
                continue
            if state.get("tc_failed"):
                _log(
                    loggers,
                    "[NET-VERIFY] pod={} node={} mode={} pid={} qdisc-failed err={}".format(
                        pod,
                        node,
                        state.get("mode", ""),
                        state.get("pid", ""),
                        state.get("tc_error", ""),
                    ),
                )
                continue
            _log(
                loggers,
                "[NET-VERIFY] pod={} node={} mode={} pid={} tc={}".format(
                    pod,
                    node,
                    state.get("mode", ""),
                    state.get("pid", ""),
                    _format_netem_metrics(state.get("metrics") or {}),
                ),
            )

        last_checks = _network_templates_satisfied(nets, last_states)
        if last_checks and all(item.get("ok") for item in last_checks):
            break
        if time.time() >= end_ts:
            break
        time.sleep(min(float(poll_interval), max(0.0, end_ts - time.time())))

    sampled_pods = 0
    tc_errors = 0
    tc_with_rules = 0
    for pod in pod_names:
        state = last_states.get(pod) or {}
        if state.get("error") == "node-missing":
            continue
        if state.get("error"):
            tc_errors += 1
            continue
        sampled_pods += 1
        if state.get("tc_failed"):
            tc_errors += 1
            continue
        if state.get("metrics"):
            tc_with_rules += 1

    verdict = "PASS" if last_checks and all(item.get("ok") for item in last_checks) else "FAIL"
    _log(
        loggers,
        "[NET-VERIFY] result={} attempts={} sampled_pods={} tc_with_rules={} tc_errors={}".format(
            verdict,
            attempts,
            sampled_pods,
            tc_with_rules,
            tc_errors,
        ),
    )
    for item in last_checks:
        _log(
            loggers,
            "[NET-VERIFY] template={} action={} observed_pods={} ok={}".format(
                item.get("template", ""),
                item.get("action", ""),
                item.get("observed_pods", []),
                item.get("ok"),
            ),
        )
    return {
        "verdict": verdict,
        "attempts": attempts,
        "sampled_pods": sampled_pods,
        "tc_with_rules": tc_with_rules,
        "tc_errors": tc_errors,
        "checks": last_checks,
    }

def verify_network_chaos_during_wait(namespace, wf_yaml_text, wait_seconds, loggers=None):
    verify_network_chaos_before_kill(namespace, wf_yaml_text, wait_seconds, loggers=loggers)
    if int(wait_seconds or 0) > 0:
        time.sleep(int(wait_seconds))





