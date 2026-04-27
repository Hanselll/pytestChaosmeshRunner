#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
import copy
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from types import SimpleNamespace

from chaos_runner import yaml_compat as yaml

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
ARTIFACTS_DIR = os.path.join(os.path.dirname(PARENT_DIR), "artifacts")
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from chaos_runner import config
from chaos_runner.workflow_factory.factory import build, build_with_resolved
from chaos_runner.executor.executor import run_workflow
from chaos_runner.executor.ems_alarm import format_recent_alarm_log_lines, run_ems_alarm_query
from chaos_runner.executor.network_verify import verify_network_chaos_before_kill
from chaos_runner.workflow_factory.postprocess import expand_network_chaos_to_component_pods
from chaos_runner.tools.k8s import kubectl_apply, kubectl_delete_workflow
from chaos_runner.tools.remote import (
    build_remote_workflow_path,
    is_remote_apply_enabled,
    kubectl_apply_remote,
    kubectl_delete_workflow_remote,
    upload_text,
)


def _tmp_path(filename):
    return os.path.join(tempfile.gettempdir(), filename)


def _log_path(filename, log_dir=""):
    base_dir = str(log_dir or "").strip()
    if not base_dir:
        base_dir = os.path.join(ARTIFACTS_DIR, "logs")
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, filename)


def write_yaml_to_tmp(wf_name, yaml_text):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = _tmp_path("{}_{}.yaml".format(wf_name, ts))
    with open(path, "w", encoding="utf-8") as f:
        f.write(yaml_text)
    return path


def _fmt_target_item(item):
    if not isinstance(item, dict):
        return str(item)

    parts = []
    if item.get("pod"):
        parts.append("pod={}".format(item.get("pod")))
    if item.get("ip"):
        parts.append("ip={}".format(item.get("ip")))
    if item.get("role"):
        parts.append("role={}".format(item.get("role")))
    if item.get("endpoint"):
        parts.append("endpoint={}".format(item.get("endpoint")))
    if item.get("note"):
        parts.append("note={}".format(item.get("note")))

    used = {"pod", "ip", "role", "endpoint", "note"}
    for key in sorted(item.keys()):
        if key in used:
            continue
        parts.append("{}={}".format(key, item.get(key)))
    return ", ".join(parts) if parts else json.dumps(item, ensure_ascii=False, sort_keys=True)


def _format_resolved_targets(resolved):
    lines = ["resolved targets:"]
    for key in sorted((resolved or {}).keys()):
        value = resolved.get(key)
        if isinstance(value, list):
            lines.append("  {} [{}]".format(key, len(value)))
            for idx, item in enumerate(value, 1):
                lines.append("    {}. {}".format(idx, _fmt_target_item(item)))
        else:
            lines.append("  {}: {}".format(key, _fmt_target_item(value)))
    return "\n".join(lines)


def _format_pod_list(title, pods):
    lines = ["{} [{}]".format(title, len(pods or []))]
    for idx, pod in enumerate((pods or []), 1):
        lines.append("  {}. {}".format(idx, pod))
    return "\n".join(lines)


def _duration_to_ms(value):
    text = str(value or "").strip().lower()
    if not text:
        return 0.0
    units = {
        "ns": 1e-6,
        "us": 1e-3,
        "ms": 1.0,
        "s": 1000.0,
        "m": 60000.0,
        "h": 3600000.0,
    }
    for unit, scale in units.items():
        if text.endswith(unit):
            return float(text[: -len(unit)] or "0") * scale
    return float(text) * 1000.0


def _format_ms(value):
    return "{:.3f}ms".format(float(value or 0.0))


def _selector_pod_names(selector):
    pods_map = ((selector or {}).get("pods") or {})
    if isinstance(pods_map, dict):
        out = []
        for pod_list in pods_map.values():
            if isinstance(pod_list, list):
                out.extend([str(item).strip() for item in pod_list if str(item).strip()])
        return out
    return []


def _extract_fault_schedule(wf_yaml_text):
    doc = yaml.safe_load(wf_yaml_text) or {}
    templates = ((doc.get("spec") or {}).get("templates") or [])
    if not templates:
        return []

    by_name = {}
    for tpl in templates:
        name = str((tpl or {}).get("name") or "").strip()
        if name:
            by_name[name] = tpl or {}

    action_names = set()
    wait_deadlines = {}
    for name, tpl in by_name.items():
        template_type = str(tpl.get("templateType") or "").strip()
        if template_type == "Suspend":
            wait_deadlines[name] = str(tpl.get("deadline") or "0s")
            continue
        pod_chaos = tpl.get("podChaos") or {}
        action = str(pod_chaos.get("action") or "").strip().lower()
        if action in ("pod-kill", "container-kill"):
            action_names.add(name)

    delays_by_action = {}
    for tpl in by_name.values():
        if str(tpl.get("templateType") or "").strip() != "Serial":
            continue
        children = [str(x).strip() for x in (tpl.get("children") or []) if str(x).strip()]
        if len(children) != 2:
            continue
        wait_name, action_name = children
        if action_name not in action_names or wait_name not in wait_deadlines:
            continue
        delays_by_action[action_name] = wait_deadlines[wait_name]

    out = []
    for name in sorted(action_names):
        tpl = by_name.get(name) or {}
        pod_chaos = tpl.get("podChaos") or {}
        action = str(pod_chaos.get("action") or "").strip().lower()
        pods = _selector_pod_names(pod_chaos.get("selector") or {})
        pod_name = pods[0] if pods else ""
        container_names = pod_chaos.get("containerNames") or []
        delay_text = delays_by_action.get(name, "0s")
        out.append(
            {
                "template": name,
                "action": action,
                "pod": pod_name,
                "container_names": list(container_names),
                "delay": delay_text,
                "delay_ms": _duration_to_ms(delay_text),
            }
        )

    out.sort(key=lambda item: (item.get("delay_ms", 0.0), item.get("action", ""), item.get("pod", "")))
    return out


def _format_fault_schedule(wf_yaml_text):
    schedule = _extract_fault_schedule(wf_yaml_text)
    if not schedule:
        return ""
    lines = ["fault schedule:"]
    prev_delay_ms = None
    for idx, item in enumerate(schedule, 1):
        delay_ms = float(item.get("delay_ms", 0.0))
        interval_ms = 0.0 if prev_delay_ms is None else max(0.0, delay_ms - prev_delay_ms)
        containers = item.get("container_names") or []
        extra = ""
        if containers:
            extra = " containers={}".format(",".join([str(x) for x in containers]))
        lines.append(
            "  {idx}. action={action} pod={pod} delay={delay} interval_from_prev={interval}{extra}".format(
                idx=idx,
                action=item.get("action", ""),
                pod=item.get("pod", ""),
                delay=_format_ms(delay_ms),
                interval=_format_ms(interval_ms),
                extra=extra,
            )
        )
        prev_delay_ms = delay_ms
    return "\n".join(lines)


def _get_observer_config(case):
    observer = case.get("observer")
    return observer if isinstance(observer, dict) else {}


def _fault_bucket(fault):
    ftype = str((fault or {}).get("type") or "").strip().lower()
    if ftype in ("network_delay", "network_loss", "network_partition"):
        return "network"
    return "kill"


def _phase_case(case, bucket, suffix):
    base = copy.deepcopy(case)
    workflow = dict(base.get("workflow") or {})
    if workflow.get("name"):
        workflow["name"] = "{}-{}".format(str(workflow.get("name")).strip(), suffix)
    if workflow:
        base["workflow"] = workflow
    if base.get("name"):
        base["name"] = "{}_{}".format(str(base.get("name")).strip(), suffix)

    if base.get("stages") is not None:
        stages = []
        for stage in (base.get("stages") or []):
            faults = [copy.deepcopy(f) for f in (stage.get("faults") or []) if _fault_bucket(f) == bucket]
            if faults:
                new_stage = dict(stage)
                new_stage["faults"] = faults
                stages.append(new_stage)
        base["stages"] = stages
        base.pop("faults", None)
        return base if stages else None

    faults = [copy.deepcopy(f) for f in (base.get("faults") or []) if _fault_bucket(f) == bucket]
    if not faults:
        return None
    base["faults"] = faults
    return base


def _render_phase_yaml(case, resolved):
    if case is None:
        return "", None
    wf_yaml, _ = build_with_resolved(case, config, resolved)
    if bool(case.get("network_expand_to_component_pods", False)):
        wf_yaml = expand_network_chaos_to_component_pods(wf_yaml, config.NS_TARGET)
    return wf_yaml, (case.get("workflow") or {}).get("name") or case.get("name") or "wf"


def _rewrite_network_chaos_deadlines(wf_yaml_text, deadline_seconds):
    if not wf_yaml_text:
        return wf_yaml_text

    doc = yaml.safe_load(wf_yaml_text) or {}
    templates = ((doc.get("spec") or {}).get("templates") or [])
    if not templates:
        return wf_yaml_text

    deadline_text = "{}s".format(max(1, int(deadline_seconds or 0)))
    changed = False
    for tpl in templates:
        if not isinstance(tpl, dict):
            continue
        if str(tpl.get("templateType") or "").strip() != "NetworkChaos":
            continue
        if tpl.get("deadline") != deadline_text:
            tpl["deadline"] = deadline_text
            changed = True
        network_chaos = tpl.get("networkChaos") or {}
        if not isinstance(network_chaos, dict):
            continue
        loss_spec = (network_chaos.get("loss") or {})
        if isinstance(loss_spec, dict) and loss_spec:
            for key in ("loss", "correlation"):
                if key in loss_spec and loss_spec.get(key) is not None and not isinstance(loss_spec.get(key), str):
                    loss_spec[key] = str(loss_spec.get(key))
                    changed = True

    if not changed:
        return wf_yaml_text
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def _write_phase_yaml(path, wf_name, suffix, yaml_text):
    if not yaml_text:
        return ""
    if path:
        root, ext = os.path.splitext(path)
        phase_path = "{}.{}.{}".format(root, suffix, ext.lstrip(".") or "yaml")
        with open(phase_path, "w", encoding="utf-8") as f:
            f.write(yaml_text)
        return phase_path
    return write_yaml_to_tmp("{}-{}".format(wf_name, suffix), yaml_text)


def _apply_workflow_once(yaml_path, yaml_text, wf_namespace, wf_name):
    if is_remote_apply_enabled():
        remote_path = build_remote_workflow_path(wf_name)
        upload_result = upload_text(remote_path, yaml_text)
        apply_result = kubectl_apply_remote(remote_path)
        return {
            "execution_mode": "remote_apply",
            "yaml_path": yaml_path,
            "remote_yaml_path": remote_path,
            "upload_result": upload_result,
            "apply_result": apply_result,
        }
    return {
        "execution_mode": "local",
        "yaml_path": yaml_path,
        "apply_result": kubectl_apply(yaml_path),
    }


def _delete_workflow_once(wf_namespace, wf_name):
    if is_remote_apply_enabled():
        return kubectl_delete_workflow_remote(wf_namespace, wf_name)
    return kubectl_delete_workflow(wf_namespace, wf_name)


def _log_execution_result(case_log, result):
    if not result:
        return

    case_log.log("[RUN] execution_mode={}".format(result.get("execution_mode", "unknown")))
    if result.get("yaml_path"):
        case_log.log("[RUN] local_yaml={}".format(result.get("yaml_path")))
    if result.get("remote_yaml_path"):
        case_log.log("[RUN] remote_yaml={}".format(result.get("remote_yaml_path")))

    upload_result = result.get("upload_result") or {}
    if upload_result:
        case_log.log("[RUN] remote_upload rc={}".format(upload_result.get("rc")))
        if upload_result.get("stderr"):
            case_log.log("[RUN] remote_upload stderr={}".format(upload_result.get("stderr")))

    apply_result = result.get("apply_result")
    if isinstance(apply_result, dict):
        case_log.log("[RUN] apply rc={}".format(apply_result.get("rc")))
        if apply_result.get("stdout"):
            case_log.log("[RUN] apply stdout={}".format(apply_result.get("stdout")))
        if apply_result.get("stderr"):
            case_log.log("[RUN] apply stderr={}".format(apply_result.get("stderr")))
    elif apply_result:
        case_log.log("[RUN] apply stdout={}".format(apply_result))

    delete_result = result.get("delete_result")
    if isinstance(delete_result, dict):
        case_log.log("[RUN] delete rc={}".format(delete_result.get("rc")))
        if delete_result.get("stdout"):
            case_log.log("[RUN] delete stdout={}".format(delete_result.get("stdout")))
        if delete_result.get("stderr"):
            case_log.log("[RUN] delete stderr={}".format(delete_result.get("stderr")))
    elif delete_result:
        case_log.log("[RUN] delete stdout={}".format(delete_result))


def _merge_lmt_commands_from_config(observer_cfg):
    lmt_cfg = dict((observer_cfg.get("lmt") or {}))

    config_shared = [x for x in (config.LMT_COMMANDS or []) if str(x).strip()]
    config_pre = [x for x in (config.LMT_PRE_COMMANDS or []) if str(x).strip()]
    config_post = [x for x in (config.LMT_POST_COMMANDS or []) if str(x).strip()]

    # Config file is explicit runtime control; it overrides case-level observer.lmt.
    if config_shared:
        lmt_cfg["commands"] = config_shared
    if config_pre:
        lmt_cfg["pre_commands"] = config_pre
    if config_post:
        lmt_cfg["post_commands"] = config_post

    out = dict(observer_cfg)
    out["lmt"] = lmt_cfg
    return out


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument(
        "--config-file",
        default="",
        help="optional YAML config file; environment variables with prefix CHAOS_RUNNER_ override file values",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="only generate workflow yaml in the system temp directory and print resolved targets; do not apply",
    )
    ap.add_argument(
        "--out",
        default="",
        help="optional: write rendered workflow yaml to this path (instead of the system temp directory)",
    )
    ap.add_argument(
        "--log-dir",
        default="",
        help="optional: write case logs to this directory (defaults to ../artifacts/logs)",
    )
    return ap.parse_args(argv)


def execute_case(case_path, config_file="", dry_run=False, out="", log_dir="", echo_stdout=True):
    args = SimpleNamespace(
        case=case_path,
        config_file=config_file,
        dry_run=bool(dry_run),
        out=out or "",
        log_dir=log_dir or "",
    )
    return execute_case_from_args(args, echo_stdout=echo_stdout)


def execute_case_from_args(args, echo_stdout=True):
    from chaos_runner.executor.observer import (
        CaseLogger,
        collect_post_case_state,
        collect_pre_case_state,
        extract_podchaos_target_pods,
        extract_target_pods_from_resolved,
        resolve_lmt_commands,
        _collect_post_common_state,
        _collect_pre_common_state,
    )

    config.load(args.config_file)

    with open(args.case, "r", encoding="utf-8") as f:
        case = yaml.safe_load(f)
    config.validate_case_dependencies(case)
    wf = case.get("workflow") or {}
    wf_name = wf.get("name") or case.get("name") or "wf"
    wf_ns = wf.get("namespace") or config.WF_NAMESPACE

    wf_yaml, resolved = build(case, config)
    network_case = _phase_case(case, "network", "net")
    kill_case = _phase_case(case, "kill", "kill")
    network_wf_yaml, network_wf_name = _render_phase_yaml(network_case, resolved)
    kill_wf_yaml, kill_wf_name = _render_phase_yaml(kill_case, resolved)

    # Keep behavior: network pod-group expansion is opt-in.
    if bool(case.get("network_expand_to_component_pods", False)):
        wf_yaml = expand_network_chaos_to_component_pods(wf_yaml, config.NS_TARGET)

    case_start_time = datetime.now()
    case_ts = case_start_time.strftime("%Y%m%d_%H%M%S_%f")[:-3]
    case_log_format_path = _log_path("chaos_case_{}_{}_format.log".format(wf_name, case_ts), getattr(args, "log_dir", ""))
    case_log_json_path = _log_path("chaos_case_{}_{}_json.log".format(wf_name, case_ts), getattr(args, "log_dir", ""))
    case_log_json_fixed_path = _log_path("chaos_case_json.log", getattr(args, "log_dir", ""))

    # Ensure each run overwrites the fixed-name json log.
    with open(case_log_json_fixed_path, "w", encoding="utf-8"):
        pass

    case_log_format = CaseLogger(case_log_format_path, echo_stdout=echo_stdout)
    case_log_json = CaseLogger(
        case_log_json_path,
        echo_stdout=False,
        mirror_paths=[case_log_json_fixed_path],
    )
    case_log_format.log("case={} namespace={} begin".format(case.get("name"), wf_ns))
    case_log_json.log("case={} namespace={} begin".format(case.get("name"), wf_ns))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(wf_yaml)
        path = args.out
    else:
        path = write_yaml_to_tmp(wf_name, wf_yaml)
    network_path = _write_phase_yaml(args.out, network_wf_name, "net", network_wf_yaml) if network_wf_yaml else ""
    kill_path = _write_phase_yaml(args.out, kill_wf_name, "kill", kill_wf_yaml) if kill_wf_yaml else ""

    print("[INFO] generated: {}".format(path))
    print("[INFO] {}".format(_format_resolved_targets(resolved)))
    print("[INFO] config-file: {}".format(config.to_dict().get("ACTIVE_CONFIG_FILE") or "<env/defaults>"))
    print("[INFO] case-log-format: {}".format(case_log_format_path))
    print("[INFO] case-log-json: {}".format(case_log_json_path))
    print("[INFO] case-log-json-fixed: {}".format(case_log_json_fixed_path))
    fault_schedule_text = _format_fault_schedule(wf_yaml)
    if fault_schedule_text:
        print("[INFO] {}".format(fault_schedule_text))

    case_log_format.log("generated workflow yaml: {}".format(path))
    case_log_format.log(_format_resolved_targets(resolved))
    case_log_json.log("generated workflow yaml: {}".format(path))
    case_log_json.log(_format_resolved_targets(resolved))
    if network_path:
        case_log_format.log("network phase workflow yaml: {}".format(network_path))
        case_log_json.log("network phase workflow yaml: {}".format(network_path))
    if kill_path:
        case_log_format.log("kill phase workflow yaml: {}".format(kill_path))
        case_log_json.log("kill phase workflow yaml: {}".format(kill_path))
    if fault_schedule_text:
        case_log_format.log(fault_schedule_text)
        case_log_json.log(fault_schedule_text)

    podchaos_target_pods = extract_podchaos_target_pods(wf_yaml, config.NS_TARGET)
    role_source_pods = extract_target_pods_from_resolved(resolved)
    observer_cfg = _merge_lmt_commands_from_config(_get_observer_config(case))
    pre_lmt_table_commands = resolve_lmt_commands(observer_cfg, "pre", "table")
    pre_lmt_json_commands = resolve_lmt_commands(observer_cfg, "pre", "json")
    post_lmt_table_commands = resolve_lmt_commands(observer_cfg, "post", "table")
    post_lmt_json_commands = resolve_lmt_commands(observer_cfg, "post", "json")
    case_log_format.log(_format_pod_list("podchaos selected pods", podchaos_target_pods))
    case_log_format.log(_format_pod_list("role-source target pods", role_source_pods))
    case_log_json.log(_format_pod_list("podchaos selected pods", podchaos_target_pods))
    case_log_json.log(_format_pod_list("role-source target pods", role_source_pods))

    pre_common = _collect_pre_common_state(config.NS_TARGET, podchaos_target_pods, role_source_pods)
    pre_common["workflow_name"] = wf_name
    pre_state_format = collect_pre_case_state(
        config.NS_TARGET,
        podchaos_target_pods,
        role_source_pods,
        case_log_format,
        lmt_mode="table",
        common_state=pre_common,
        lmt_commands=pre_lmt_table_commands,
    )
    pre_state_json = collect_pre_case_state(
        config.NS_TARGET,
        podchaos_target_pods,
        role_source_pods,
        case_log_json,
        lmt_mode="json",
        common_state=pre_common,
        lmt_commands=pre_lmt_json_commands,
    )

    if args.dry_run:
        print("[DRY-RUN] skip run_workflow()")
        case_log_format.log("dry-run mode, skip workflow apply")
        case_log_json.log("dry-run mode, skip workflow apply")
        return {
            "case_name": case.get("name"),
            "workflow_name": wf_name,
            "workflow_namespace": wf_ns,
            "workflow_yaml_path": path,
            "resolved_targets": resolved,
            "case_log_format_path": case_log_format_path,
            "case_log_json_path": case_log_json_path,
            "case_log_json_fixed_path": case_log_json_fixed_path,
            "dry_run": True,
        }

    wait_seconds = int(case.get("wait_seconds", config.DEFAULT_WAIT_SECONDS))
    cleanup = bool(case.get("cleanup", config.DELETE_WORKFLOW_AFTER))
    net_verify_timeout = int(getattr(config, "NET_VERIFY_TIMEOUT_SECONDS", 20) or 20)
    network_phase_buffer = int(getattr(config, "NETWORK_PHASE_EXTRA_BUFFER_SECONDS", 10) or 0)
    network_execution_result = None
    kill_execution_result = None
    net_cleanup_result = None
    ems_alarm_result = None
    network_cleanup_allowed = False

    try:
        if network_wf_yaml:
            network_deadline_seconds = net_verify_timeout + wait_seconds + max(0, network_phase_buffer)
            network_wf_yaml = _rewrite_network_chaos_deadlines(network_wf_yaml, network_deadline_seconds)
            if network_path:
                with open(network_path, "w", encoding="utf-8") as f:
                    f.write(network_wf_yaml)
            case_log_format.log("[PHASE] network deadline extended deadline_seconds={}".format(network_deadline_seconds))
            case_log_json.log("[PHASE] network deadline extended deadline_seconds={}".format(network_deadline_seconds))
            case_log_format.log("[PHASE] apply network workflow")
            case_log_json.log("[PHASE] apply network workflow")
            network_execution_result = _apply_workflow_once(network_path, network_wf_yaml, wf_ns, network_wf_name)
            _log_execution_result(case_log_format, network_execution_result)
            _log_execution_result(case_log_json, network_execution_result)

            case_log_format.log("[PHASE] verify network tc rules")
            case_log_json.log("[PHASE] verify network tc rules")
            verify_result = verify_network_chaos_before_kill(
                config.NS_TARGET,
                network_wf_yaml,
                net_verify_timeout,
                loggers=[case_log_format, case_log_json],
            )
            if (verify_result or {}).get("verdict") == "FAIL":
                raise RuntimeError("network verify failed before kill phase")
            case_log_format.log("[PHASE] network verify passed, keep network chaos active")
            case_log_json.log("[PHASE] network verify passed, keep network chaos active")

        if kill_wf_yaml:
            case_log_format.log("[PHASE] apply kill workflow")
            case_log_json.log("[PHASE] apply kill workflow")
            case_log_format.log("[PHASE] wait window started wait_seconds={}".format(wait_seconds))
            case_log_json.log("[PHASE] wait window started wait_seconds={}".format(wait_seconds))
            kill_execution_result = run_workflow(
                kill_path,
                wf_ns,
                kill_wf_name,
                wait_seconds,
                cleanup=cleanup,
                yaml_text=kill_wf_yaml,
            )
            _log_execution_result(case_log_format, kill_execution_result)
            _log_execution_result(case_log_json, kill_execution_result)
            network_cleanup_allowed = True
        elif network_wf_yaml:
            case_log_format.log("[PHASE] wait window started after network verify wait_seconds={}".format(wait_seconds))
            case_log_json.log("[PHASE] wait window started after network verify wait_seconds={}".format(wait_seconds))
            time.sleep(wait_seconds)
            kill_execution_result = {
                "execution_mode": "network_only_wait",
                "yaml_path": network_path,
                "wait_seconds": wait_seconds,
            }
            network_cleanup_allowed = True
        else:
            case_log_format.log("[PHASE] apply single workflow")
            case_log_json.log("[PHASE] apply single workflow")
            case_log_format.log("[PHASE] wait window started wait_seconds={}".format(wait_seconds))
            case_log_json.log("[PHASE] wait window started wait_seconds={}".format(wait_seconds))
            kill_execution_result = run_workflow(
                path,
                wf_ns,
                wf_name,
                wait_seconds,
                cleanup=cleanup,
                yaml_text=wf_yaml,
            )
            _log_execution_result(case_log_format, kill_execution_result)
            _log_execution_result(case_log_json, kill_execution_result)
    finally:
        if network_wf_yaml and cleanup and network_cleanup_allowed:
            case_log_format.log("[PHASE] cleanup network workflow")
            case_log_json.log("[PHASE] cleanup network workflow")
            net_cleanup_result = _delete_workflow_once(wf_ns, network_wf_name)
            case_log_format.log("[RUN] net cleanup result={}".format(net_cleanup_result))
            case_log_json.log("[RUN] net cleanup result={}".format(net_cleanup_result))
        post_common = _collect_post_common_state(config.NS_TARGET, pre_state_format)
        collect_post_case_state(
            config.NS_TARGET,
            pre_state_format,
            case_log_format,
            lmt_mode="table",
            common_state=post_common,
            lmt_commands=post_lmt_table_commands,
        )
        collect_post_case_state(
            config.NS_TARGET,
            pre_state_json,
            case_log_json,
            lmt_mode="json",
            common_state=post_common,
            lmt_commands=post_lmt_json_commands,
        )
        prior_exception_active = sys.exc_info()[0] is not None
        try:
            ems_alarm_result = run_ems_alarm_query(wf_name, case_ts, getattr(args, "log_dir", ""), since_time=case_start_time)
            if ems_alarm_result and not ems_alarm_result.get("skipped"):
                msg = "[EMS] alarm query target={} output_dir={} counts={}".format(
                    ems_alarm_result.get("target", ""),
                    ems_alarm_result.get("output_dir", ""),
                    ems_alarm_result.get("counts", {}),
                )
                case_log_format.log(msg)
                case_log_json.log(msg)
                for line in format_recent_alarm_log_lines(ems_alarm_result):
                    case_log_format.log(line)
                    case_log_json.log(line)
            elif ems_alarm_result:
                case_log_format.log("[EMS] alarm query skipped: {}".format(ems_alarm_result.get("reason", "")))
                case_log_json.log("[EMS] alarm query skipped: {}".format(ems_alarm_result.get("reason", "")))
        except Exception as exc:
            ems_alarm_result = {"enabled": True, "skipped": False, "error": str(exc)}
            case_log_format.log("[EMS] alarm query failed: {}".format(exc))
            case_log_json.log("[EMS] alarm query failed: {}".format(exc))
            if not prior_exception_active:
                raise
        case_log_format.log("case finished")
        case_log_json.log("case finished")

    print("[DONE] case:", case.get("name"))
    return {
        "case_name": case.get("name"),
        "workflow_name": wf_name,
        "workflow_namespace": wf_ns,
        "workflow_yaml_path": path,
        "resolved_targets": resolved,
        "case_log_format_path": case_log_format_path,
        "case_log_json_path": case_log_json_path,
        "case_log_json_fixed_path": case_log_json_fixed_path,
        "dry_run": False,
        "network_execution_result": network_execution_result,
        "kill_execution_result": kill_execution_result,
        "network_cleanup_result": net_cleanup_result,
        "ems_alarm_result": ems_alarm_result,
    }


def main(argv=None):
    args = parse_args(argv)
    execute_case_from_args(args, echo_stdout=True)


if __name__ == "__main__":
    main()




