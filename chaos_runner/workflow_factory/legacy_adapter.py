# -*- coding: utf-8 -*-
import copy


_STRESS_RENDERERS = {
    "cpu_stress_parallel": "cpu_stress",
    "cpu_stress_single_role": "cpu_stress",
    "memory_stress_parallel": "memory_stress",
    "memory_stress_single_role": "memory_stress",
}


def _common_case(case):
    out = {}
    for key in ("name", "workflow", "targets", "wait_seconds", "cleanup", "network_expand_to_component_pods"):
        if key in case:
            out[key] = copy.deepcopy(case.get(key))
    out["renderer"] = "modular_chaos"
    return out


def _resolve_network_duration(net, default=None):
    for key in ("deadline_seconds", "deadline_sec", "duration"):
        val = net.get(key)
        if val is not None and val != "":
            return copy.deepcopy(val)
    return default


def _network_payload(net, default_selectors=None, default_labels=None):
    payload = {}

    labels = copy.deepcopy(net.get("labels") or {})
    if default_labels:
        labels.setdefault("from", default_labels.get("from"))
        labels.setdefault("to", default_labels.get("to"))
    if labels.get("from") or labels.get("to"):
        payload["labels"] = labels

    selectors = copy.deepcopy(net.get("selectors") or {})
    if default_selectors:
        selectors.setdefault("from", default_selectors.get("from"))
        selectors.setdefault("to", default_selectors.get("to"))
        if default_selectors.get("from_expand") is not None:
            selectors.setdefault("from_expand", default_selectors.get("from_expand"))
        if default_selectors.get("to_expand") is not None:
            selectors.setdefault("to_expand", default_selectors.get("to_expand"))
    if selectors.get("from") or selectors.get("to"):
        payload["selectors"] = selectors

    return payload


def _network_actions(net, default_actions):
    actions = net.get("actions")
    if actions:
        return [str(x).strip().lower() for x in actions]

    action = str(net.get("action") or "").strip().lower()
    if not action:
        return list(default_actions)
    if action in ("both", "delay+loss", "loss+delay"):
        return ["delay", "loss"]
    return [action]


def _delay_config(net):
    data = copy.deepcopy(net.get("delay") or {})
    if not isinstance(data, dict):
        data = {}
    if net.get("latency") is not None and "latency" not in data:
        data["latency"] = copy.deepcopy(net.get("latency"))
    if net.get("jitter") is not None and "jitter" not in data:
        data["jitter"] = copy.deepcopy(net.get("jitter"))
    return data


def _loss_config(net):
    raw = net.get("loss")
    data = copy.deepcopy(raw if isinstance(raw, dict) else {})
    if not isinstance(data, dict):
        data = {}
    if raw is not None and not isinstance(raw, dict) and "loss" not in data:
        data["loss"] = copy.deepcopy(raw)
    if net.get("corr") is not None and "correlation" not in data:
        data["correlation"] = copy.deepcopy(net.get("corr"))
    return data


def _build_network_faults(net, default_actions, default_selectors=None, default_labels=None):
    actions = _network_actions(net, default_actions)
    payload = _network_payload(net, default_selectors=default_selectors, default_labels=default_labels)
    faults = []
    duration = _resolve_network_duration(net)
    direction = copy.deepcopy(net.get("direction"))

    for action in actions:
        fault = {"type": "network_{}".format(action)}
        fault.update(copy.deepcopy(payload))
        if duration is not None:
            fault["duration"] = duration
        if direction:
            fault["direction"] = direction

        if action == "delay":
            delay = _delay_config(net)
            if delay:
                fault["delay"] = delay
        elif action == "loss":
            loss = _loss_config(net)
            if loss:
                fault["loss"] = loss
        elif action != "partition":
            raise RuntimeError("unsupported legacy network action: {}".format(action))

        faults.append(fault)
    return faults


def _adapt_parallel_podkill(case):
    out = _common_case(case)
    faults = []
    for item in (case.get("kill") or {}).get("items") or []:
        fault = {
            "type": "pod_kill",
            "target": item.get("target"),
        }
        if item.get("expand") is not None:
            fault["expand"] = copy.deepcopy(item.get("expand"))
        if item.get("delay") is not None:
            fault["delay"] = copy.deepcopy(item.get("delay"))
        faults.append(fault)
    out["mode"] = "parallel"
    out["faults"] = faults
    return out


def _adapt_podkill_then_network(case):
    kill_targets = (case.get("kill") or {}).get("targets") or []
    net = case.get("network") or {}
    out = _common_case(case)
    out["stages"] = [
        {
            "mode": "parallel",
            "faults": [{"type": "pod_kill", "target": target_id} for target_id in kill_targets],
        },
        {
            "mode": "parallel",
            "faults": _build_network_faults(
                net,
                default_actions=["delay", "loss"],
                default_labels={
                    "from": net.get("upc_label_kv"),
                    "to": net.get("rc_label_kv"),
                },
            ),
        },
    ]
    return out


def _adapt_network_then_parallel_podkill(case):
    out = _common_case(case)
    faults = _build_network_faults(case.get("network") or {}, default_actions=["delay"])
    for item in (case.get("kill") or {}).get("items") or []:
        fault = {
            "type": "pod_kill",
            "target": item.get("target"),
        }
        if item.get("expand") is not None:
            fault["expand"] = copy.deepcopy(item.get("expand"))
        if item.get("delay") is not None:
            fault["delay"] = copy.deepcopy(item.get("delay"))
        faults.append(fault)
    out["mode"] = "parallel"
    out["faults"] = faults
    return out


def _adapt_network_parallel_containerkill(case):
    out = _common_case(case)
    faults = _build_network_faults(case.get("network") or {}, default_actions=["delay"])
    kill = case.get("kill") or {}
    default_container_names = copy.deepcopy(kill.get("containerNames"))
    for item in kill.get("items") or []:
        fault = {
            "type": "container_kill",
            "target": item.get("target"),
        }
        if item.get("expand") is not None:
            fault["expand"] = copy.deepcopy(item.get("expand"))
        if item.get("delay") is not None:
            fault["delay"] = copy.deepcopy(item.get("delay"))
        if item.get("containerMap") is not None:
            fault["containerMap"] = copy.deepcopy(item.get("containerMap"))
        if item.get("containerNames") is not None:
            fault["containerNames"] = copy.deepcopy(item.get("containerNames"))
        elif default_container_names is not None:
            fault["containerNames"] = copy.deepcopy(default_container_names)
        faults.append(fault)
    out["mode"] = "parallel"
    out["faults"] = faults
    return out


def _adapt_stress(case, fault_type):
    out = _common_case(case)
    stress = case.get("stress") or {}
    faults = []

    targets = stress.get("targets")
    if targets is None:
        targets = [
            {
                "target": stress.get("target"),
                "expand": stress.get("expand"),
                "duration": stress.get("duration"),
                "cpu": stress.get("cpu"),
                "memory": stress.get("memory"),
            }
        ]

    for item in targets:
        fault = {
            "type": fault_type,
            "target": item.get("target"),
        }
        duration = item.get("duration", stress.get("duration"))
        if duration is not None:
            fault["duration"] = copy.deepcopy(duration)
        if item.get("expand") is not None:
            fault["expand"] = copy.deepcopy(item.get("expand"))

        if fault_type == "cpu_stress":
            cpu = copy.deepcopy(stress.get("cpu") or {})
            cpu.update(copy.deepcopy(item.get("cpu") or {}))
            if cpu:
                fault["cpu"] = cpu
        else:
            memory = copy.deepcopy(stress.get("memory") or {})
            memory.update(copy.deepcopy(item.get("memory") or {}))
            if memory:
                fault["memory"] = memory
        faults.append(fault)

    out["mode"] = "parallel"
    out["faults"] = faults
    return out


def adapt_case_to_modular(case):
    renderer = str(case.get("renderer") or "").strip()
    if renderer == "modular_chaos":
        return case
    if renderer == "parallel_podkill":
        return _adapt_parallel_podkill(case)
    if renderer == "podkill_then_network":
        return _adapt_podkill_then_network(case)
    if renderer == "network_then_parallel_podkill":
        return _adapt_network_then_parallel_podkill(case)
    if renderer == "network_parallel_containerkill":
        return _adapt_network_parallel_containerkill(case)
    if renderer in _STRESS_RENDERERS:
        return _adapt_stress(case, _STRESS_RENDERERS[renderer])
    return case
