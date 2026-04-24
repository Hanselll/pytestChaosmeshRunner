# -*- coding: utf-8 -*-


SUPPORTED_RENDERERS = {
    "parallel_podkill",
    "podkill_then_network",
    "network_then_parallel_podkill",
    "network_parallel_containerkill",
    "cpu_stress_parallel",
    "cpu_stress_single_role",
    "memory_stress_parallel",
    "memory_stress_single_role",
    "modular_chaos",
}

KNOWN_FINDERS = {
    "upc_talker",
    "upc_non_talkers",
    "upc_pods",
    "rc_leader",
    "rc_followers",
    "rc_pods",
    "rc_etcd_leader",
    "etcd_followers",
    "etcd_pods",
    "ddb_masters",
    "ddb_non_masters",
    "ddb_pods",
    "ddb_shard_master",
    "ddb_shard_slaves",
    "ddb_other_shard_pods",
    "ddb_shard_master_peers",
    "sdb_master",
    "sdb_slaves",
    "sdb_sentinel_info",
    "smf_pods",
    "sdb_pods",
    "by_label",
    "by_label_values",
    "by_label_prefix",
    "by_name_prefix",
}

MODULAR_FAULT_TYPES = {
    "pod_kill",
    "container_kill",
    "network_delay",
    "network_loss",
    "network_partition",
    "cpu_stress",
    "memory_stress",
}


class CaseValidationError(RuntimeError):
    pass


def _fail(path, message):
    raise CaseValidationError("{}: {}".format(path or "case", message))


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require_mapping(value, path):
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    return value


def _require_list(value, path):
    if not isinstance(value, list):
        _fail(path, "must be a list")
    return value


def _require_non_empty_string(value, path):
    if not isinstance(value, str) or not value.strip():
        _fail(path, "must be a non-empty string")
    return value.strip()


def _validate_bool(value, path):
    if value is None:
        return
    if not isinstance(value, bool):
        _fail(path, "must be a boolean")


def _validate_int(value, path, allow_zero=True):
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool):
        _fail(path, "must be an integer")
    if value < 0 or (not allow_zero and value == 0):
        _fail(path, "must be >= {}".format(0 if allow_zero else 1))


def _validate_string_list(value, path):
    if value is None:
        return
    items = _require_list(value, path)
    for idx, item in enumerate(items):
        _require_non_empty_string(item, "{}[{}]".format(path, idx))


def _validate_label_selector_string(value, path):
    text = _require_non_empty_string(value, path)
    if ":" not in text and "=" not in text:
        _fail(path, "must use 'key: value' or 'key=value' format")


def _validate_container_map(value, path):
    if value is None:
        return
    mapping = _require_mapping(value, path)
    for pod_name, container_names in mapping.items():
        if not isinstance(pod_name, str) or not pod_name.strip():
            _fail(path, "keys must be non-empty pod names")
        _validate_string_list(container_names, "{}.{}".format(path, pod_name))


def _validate_scalar_or_range(value, path):
    if value is None or value == "":
        return
    if _is_number(value) or isinstance(value, str):
        return
    if isinstance(value, dict):
        if "min" not in value or "max" not in value:
            _fail(path, "range object must include min and max")
        if not (_is_number(value.get("min")) or isinstance(value.get("min"), str)):
            _fail(path + ".min", "must be a number or string")
        if not (_is_number(value.get("max")) or isinstance(value.get("max"), str)):
            _fail(path + ".max", "must be a number or string")
        return
    _fail(path, "must be a number, string, or {min,max} object")


def _validate_expand(value, path):
    if value is None or value == "all":
        return
    expand = _require_mapping(value, path)
    if "indices" in expand:
        idxs = _require_list(expand.get("indices"), path + ".indices")
        if not idxs:
            _fail(path + ".indices", "must not be empty")
        for idx, item in enumerate(idxs):
            if not isinstance(item, int) or isinstance(item, bool) or item < 0:
                _fail("{}[{}]".format(path + ".indices", idx), "must be a non-negative integer")
        return
    mode = _require_non_empty_string(expand.get("mode"), path + ".mode").lower()
    if mode != "random":
        _fail(path + ".mode", "unsupported mode {!r}".format(mode))
    count = expand.get("count")
    if count is None:
        _fail(path + ".count", "is required when mode=random")
    _validate_int(count, path + ".count", allow_zero=False)
    seed = expand.get("seed")
    if seed is not None and not _is_number(seed) and not isinstance(seed, str):
        _fail(path + ".seed", "must be a number or string")


def _validate_target_ref(target_id, path, target_ids):
    tid = _require_non_empty_string(target_id, path)
    if tid not in target_ids:
        _fail(path, "unknown target id {!r}".format(tid))


def _validate_targets(case):
    targets = case.get("targets") or []
    _require_list(targets, "targets")
    seen = set()
    for idx, item in enumerate(targets):
        path = "targets[{}]".format(idx)
        target = _require_mapping(item, path)
        tid = _require_non_empty_string(target.get("id"), path + ".id")
        if tid in seen:
            _fail(path + ".id", "duplicate target id {!r}".format(tid))
        seen.add(tid)

        finder = _require_non_empty_string(target.get("finder"), path + ".finder")
        if finder not in KNOWN_FINDERS:
            _fail(path + ".finder", "unsupported finder {!r}".format(finder))

        if finder == "by_label":
            label = _require_non_empty_string(target.get("label"), path + ".label")
            if ":" not in label:
                _fail(path + ".label", "must use 'key: value' format")
        elif finder == "by_label_values":
            _require_non_empty_string(target.get("label_key"), path + ".label_key")
            label_values = _require_list(target.get("label_values"), path + ".label_values")
            if not label_values:
                _fail(path + ".label_values", "must not be empty")
            for value_idx, value in enumerate(label_values):
                _require_non_empty_string(value, "{}[{}]".format(path + ".label_values", value_idx))
        elif finder == "by_label_prefix":
            label_prefix = target.get("label_prefix") or target.get("label")
            label_prefix = _require_non_empty_string(label_prefix, path + ".label_prefix")
            if ":" not in label_prefix:
                _fail(path + ".label_prefix", "must use 'key: prefix' format")
        elif finder == "by_name_prefix":
            name_prefix = target.get("name_prefix") or target.get("prefix") or target.get("name")
            _require_non_empty_string(name_prefix, path + ".name_prefix")
        elif finder in ("ddb_shard_master", "ddb_shard_slaves", "ddb_other_shard_pods", "ddb_shard_master_peers"):
            _require_non_empty_string(target.get("shard"), path + ".shard")
        elif finder == "ddb_pods":
            role = target.get("role")
            if role is not None:
                _require_non_empty_string(role, path + ".role")
            shard_scope = str(target.get("shard_scope", "all")).strip().lower()
            if shard_scope not in ("all", "in", "not_in"):
                _fail(path + ".shard_scope", "must be one of all/in/not_in")
            if shard_scope in ("in", "not_in"):
                _require_non_empty_string(target.get("shard"), path + ".shard")
    return seen


def _validate_workflow(case):
    workflow = case.get("workflow") or {}
    _require_mapping(workflow, "workflow")
    if not ((case.get("name") or "").strip() or (workflow.get("name") or "").strip()):
        _fail("name", "case.name or workflow.name is required")
    if workflow.get("name") is not None:
        _require_non_empty_string(workflow.get("name"), "workflow.name")
    if workflow.get("namespace") is not None:
        _require_non_empty_string(workflow.get("namespace"), "workflow.namespace")


def _validate_common(case):
    if not isinstance(case, dict):
        _fail("case", "must be an object")
    renderer = _require_non_empty_string(case.get("renderer"), "renderer")
    if renderer not in SUPPORTED_RENDERERS:
        _fail("renderer", "unsupported renderer {!r}".format(renderer))

    _validate_workflow(case)
    _validate_targets(case)
    _validate_int(case.get("wait_seconds"), "wait_seconds")
    _validate_bool(case.get("cleanup"), "cleanup")
    _validate_bool(case.get("network_expand_to_component_pods"), "network_expand_to_component_pods")
    _validate_observer(case.get("observer"))
    return renderer


def _validate_lmt_commands(value, path):
    _validate_string_list(value, path)
    for idx, item in enumerate(value or []):
        cmd = _require_non_empty_string(item, "{}[{}]".format(path, idx))
        if "lmt-cli" not in cmd:
            _fail("{}[{}]".format(path, idx), "must contain an lmt-cli command")


def _validate_observer(value):
    if value is None:
        return
    observer = _require_mapping(value, "observer")
    lmt = observer.get("lmt")
    if lmt is None:
        return
    lmt_cfg = _require_mapping(lmt, "observer.lmt")
    _validate_lmt_commands(lmt_cfg.get("commands"), "observer.lmt.commands")
    _validate_lmt_commands(lmt_cfg.get("pre_commands"), "observer.lmt.pre_commands")
    _validate_lmt_commands(lmt_cfg.get("post_commands"), "observer.lmt.post_commands")


def _validate_kill_items(items, path, target_ids, require_container_names=False):
    arr = _require_list(items, path)
    if not arr:
        _fail(path, "must not be empty")
    for idx, item in enumerate(arr):
        item_path = "{}[{}]".format(path, idx)
        data = _require_mapping(item, item_path)
        _validate_target_ref(data.get("target"), item_path + ".target", target_ids)
        _validate_expand(data.get("expand"), item_path + ".expand")
        _validate_scalar_or_range(data.get("delay"), item_path + ".delay")
        _validate_string_list(data.get("containerNames"), item_path + ".containerNames")
        _validate_container_map(data.get("containerMap"), item_path + ".containerMap")
        if require_container_names and not (data.get("containerNames") or data.get("containerMap")):
            _fail(item_path, "containerNames or containerMap is required")


def _validate_network_selector_block(obj, path, target_ids, labels_required=False):
    labels = obj.get("labels")
    selectors = obj.get("selectors")
    if not labels and not selectors:
        _fail(path, "requires selectors or labels")

    has_from = False
    has_to = False

    if labels is not None:
        label_map = _require_mapping(labels, path + ".labels")
        if label_map.get("from") is not None:
            _validate_label_selector_string(label_map.get("from"), path + ".labels.from")
            has_from = True
        if label_map.get("to") is not None:
            _validate_label_selector_string(label_map.get("to"), path + ".labels.to")
            has_to = True

    if selectors is not None:
        selector_map = _require_mapping(selectors, path + ".selectors")
        if selector_map.get("from") is not None:
            _validate_target_ref(selector_map.get("from"), path + ".selectors.from", target_ids)
            has_from = True
        if selector_map.get("to") is not None:
            _validate_target_ref(selector_map.get("to"), path + ".selectors.to", target_ids)
            has_to = True
        _validate_expand(selector_map.get("from_expand"), path + ".selectors.from_expand")
        _validate_expand(selector_map.get("to_expand"), path + ".selectors.to_expand")

    if labels_required and labels is None:
        _fail(path + ".labels", "is required for this renderer")

    if labels_required and labels is not None:
        label_map = labels
        _validate_label_selector_string(label_map.get("from"), path + ".labels.from")
        _validate_label_selector_string(label_map.get("to"), path + ".labels.to")

    if not has_from:
        _fail(path, "missing 'from' selector/label")
    if not has_to:
        _fail(path, "missing 'to' selector/label")

    if obj.get("direction") is not None:
        _require_non_empty_string(obj.get("direction"), path + ".direction")


def _validate_network_action_config(net, path):
    _validate_scalar_or_range(net.get("duration"), path + ".duration")
    _validate_scalar_or_range(net.get("deadline_seconds"), path + ".deadline_seconds")
    _validate_scalar_or_range(net.get("deadline_sec"), path + ".deadline_sec")
    _validate_scalar_or_range(net.get("latency"), path + ".latency")
    _validate_scalar_or_range(net.get("jitter"), path + ".jitter")
    _validate_scalar_or_range(net.get("corr"), path + ".corr")

    delay = net.get("delay")
    if delay is not None:
        delay_map = _require_mapping(delay, path + ".delay")
        _validate_scalar_or_range(delay_map.get("latency"), path + ".delay.latency")
        _validate_scalar_or_range(delay_map.get("jitter"), path + ".delay.jitter")

    loss = net.get("loss")
    if isinstance(loss, dict):
        loss_map = _require_mapping(loss, path + ".loss")
        _validate_scalar_or_range(loss_map.get("loss"), path + ".loss.loss")
        _validate_scalar_or_range(loss_map.get("correlation"), path + ".loss.correlation")
        _validate_scalar_or_range(loss_map.get("corr"), path + ".loss.corr")
    else:
        _validate_scalar_or_range(loss, path + ".loss")

    actions = net.get("actions")
    if actions is not None:
        arr = _require_list(actions, path + ".actions")
        if not arr:
            _fail(path + ".actions", "must not be empty")
        for idx, action in enumerate(arr):
            act = _require_non_empty_string(action, "{}[{}]".format(path + ".actions", idx)).lower()
            if act not in ("delay", "loss", "partition"):
                _fail("{}[{}]".format(path + ".actions", idx), "unsupported action {!r}".format(act))
    if net.get("action") is not None:
        _require_non_empty_string(net.get("action"), path + ".action")


def _validate_parallel_podkill(case, target_ids):
    kill = _require_mapping(case.get("kill"), "kill")
    _validate_kill_items(kill.get("items"), "kill.items", target_ids)


def _validate_podkill_then_network(case, target_ids):
    kill = _require_mapping(case.get("kill"), "kill")
    targets = _require_list(kill.get("targets"), "kill.targets")
    if len(targets) < 2:
        _fail("kill.targets", "must include at least two targets")
    for idx, target_id in enumerate(targets):
        _validate_target_ref(target_id, "kill.targets[{}]".format(idx), target_ids)

    net = _require_mapping(case.get("network"), "network")
    _validate_label_selector_string(net.get("upc_label_kv"), "network.upc_label_kv")
    _validate_label_selector_string(net.get("rc_label_kv"), "network.rc_label_kv")
    _validate_network_action_config(net, "network")


def _validate_network_then_parallel_podkill(case, target_ids):
    net = _require_mapping(case.get("network"), "network")
    _validate_network_selector_block(net, "network", target_ids)
    _validate_network_action_config(net, "network")

    kill = _require_mapping(case.get("kill"), "kill")
    _validate_kill_items(kill.get("items"), "kill.items", target_ids)


def _validate_network_parallel_containerkill(case, target_ids):
    net = _require_mapping(case.get("network"), "network")
    _validate_network_selector_block(net, "network", target_ids)
    _validate_network_action_config(net, "network")

    kill = _require_mapping(case.get("kill"), "kill")
    default_container_names = kill.get("containerNames")
    _validate_string_list(default_container_names, "kill.containerNames")
    items = _require_list(kill.get("items"), "kill.items")
    if not items:
        _fail("kill.items", "must not be empty")
    for idx, item in enumerate(items):
        path = "kill.items[{}]".format(idx)
        data = _require_mapping(item, path)
        _validate_target_ref(data.get("target"), path + ".target", target_ids)
        _validate_expand(data.get("expand"), path + ".expand")
        _validate_scalar_or_range(data.get("delay"), path + ".delay")
        _validate_string_list(data.get("containerNames"), path + ".containerNames")
        _validate_container_map(data.get("containerMap"), path + ".containerMap")
        if not (default_container_names or data.get("containerNames") or data.get("containerMap")):
            _fail(path, "containerNames or containerMap is required")


def _validate_stress_target(item, path, target_ids):
    data = _require_mapping(item, path)
    _validate_target_ref(data.get("target"), path + ".target", target_ids)
    _validate_expand(data.get("expand"), path + ".expand")
    _validate_scalar_or_range(data.get("duration"), path + ".duration")
    if data.get("cpu") is not None:
        cpu = _require_mapping(data.get("cpu"), path + ".cpu")
        _validate_int(cpu.get("workers"), path + ".cpu.workers", allow_zero=False)
        _validate_int(cpu.get("load"), path + ".cpu.load", allow_zero=False)
    if data.get("memory") is not None:
        mem = _require_mapping(data.get("memory"), path + ".memory")
        _validate_int(mem.get("workers"), path + ".memory.workers", allow_zero=False)
        if mem.get("size") is not None:
            _require_non_empty_string(mem.get("size"), path + ".memory.size")


def _validate_stress_renderer(case, target_ids):
    stress = _require_mapping(case.get("stress"), "stress")
    _validate_scalar_or_range(stress.get("duration"), "stress.duration")
    if stress.get("cpu") is not None:
        _require_mapping(stress.get("cpu"), "stress.cpu")
    if stress.get("memory") is not None:
        _require_mapping(stress.get("memory"), "stress.memory")

    targets = stress.get("targets")
    target = stress.get("target")
    if targets is None and target is None:
        _fail("stress", "stress.target or stress.targets is required")
    if targets is not None:
        arr = _require_list(targets, "stress.targets")
        if not arr:
            _fail("stress.targets", "must not be empty")
        for idx, item in enumerate(arr):
            _validate_stress_target(item, "stress.targets[{}]".format(idx), target_ids)
    else:
        _validate_target_ref(target, "stress.target", target_ids)
        _validate_expand(stress.get("expand"), "stress.expand")


def _validate_fault(fault, path, target_ids):
    data = _require_mapping(fault, path)
    ftype = _require_non_empty_string(data.get("type"), path + ".type")
    if ftype not in MODULAR_FAULT_TYPES:
        _fail(path + ".type", "unsupported fault type {!r}".format(ftype))

    if ftype in ("pod_kill", "container_kill", "cpu_stress", "memory_stress"):
        _validate_target_ref(data.get("target"), path + ".target", target_ids)
        _validate_expand(data.get("expand"), path + ".expand")
        _validate_scalar_or_range(data.get("duration"), path + ".duration")

    if ftype == "pod_kill":
        _validate_scalar_or_range(data.get("delay"), path + ".delay")
        return

    if ftype == "container_kill":
        _validate_scalar_or_range(data.get("delay"), path + ".delay")
        _validate_string_list(data.get("containerNames"), path + ".containerNames")
        _validate_container_map(data.get("containerMap"), path + ".containerMap")
        if not (data.get("containerNames") or data.get("containerMap")):
            _fail(path, "containerNames or containerMap is required")
        return

    if ftype in ("cpu_stress", "memory_stress"):
        if ftype == "cpu_stress":
            cpu = _require_mapping(data.get("cpu") or {}, path + ".cpu")
            if cpu:
                _validate_int(cpu.get("workers"), path + ".cpu.workers", allow_zero=False)
                _validate_int(cpu.get("load"), path + ".cpu.load", allow_zero=False)
        else:
            mem = _require_mapping(data.get("memory") or {}, path + ".memory")
            if mem:
                _validate_int(mem.get("workers"), path + ".memory.workers", allow_zero=False)
                if mem.get("size") is not None:
                    _require_non_empty_string(mem.get("size"), path + ".memory.size")
        return

    _validate_network_selector_block(data, path, target_ids)
    _validate_scalar_or_range(data.get("duration"), path + ".duration")
    if data.get("direction") is not None:
        _require_non_empty_string(data.get("direction"), path + ".direction")

    if ftype == "network_delay":
        delay = _require_mapping(data.get("delay") or {}, path + ".delay")
        if delay:
            _validate_scalar_or_range(delay.get("latency"), path + ".delay.latency")
            _validate_scalar_or_range(delay.get("jitter"), path + ".delay.jitter")
        return

    if ftype == "network_loss":
        loss = _require_mapping(data.get("loss") or {}, path + ".loss")
        if loss:
            _validate_scalar_or_range(loss.get("loss"), path + ".loss.loss")
            _validate_scalar_or_range(loss.get("correlation"), path + ".loss.correlation")


def _validate_modular_case(case, target_ids):
    has_faults = case.get("faults") is not None
    has_stages = case.get("stages") is not None
    if has_faults and has_stages:
        _fail("case", "use either faults or stages, not both")
    if not has_faults and not has_stages:
        _fail("case", "modular_chaos requires faults or stages")

    if has_faults:
        faults = _require_list(case.get("faults"), "faults")
        if not faults:
            _fail("faults", "must not be empty")
        mode = case.get("mode")
        if mode is not None:
            mode = _require_non_empty_string(mode, "mode").lower()
            if mode not in ("parallel", "serial"):
                _fail("mode", "must be parallel or serial")
        for idx, fault in enumerate(faults):
            _validate_fault(fault, "faults[{}]".format(idx), target_ids)
        return

    stages = _require_list(case.get("stages"), "stages")
    if not stages:
        _fail("stages", "must not be empty")
    for idx, stage in enumerate(stages):
        path = "stages[{}]".format(idx)
        stage_data = _require_mapping(stage, path)
        mode = _require_non_empty_string(stage_data.get("mode", "parallel"), path + ".mode").lower()
        if mode not in ("parallel", "serial"):
            _fail(path + ".mode", "must be parallel or serial")
        faults = _require_list(stage_data.get("faults"), path + ".faults")
        if not faults:
            _fail(path + ".faults", "must not be empty")
        for fault_idx, fault in enumerate(faults):
            _validate_fault(fault, "{}.faults[{}]".format(path, fault_idx), target_ids)


def validate_case(case):
    renderer = _validate_common(case)
    target_ids = _validate_targets(case)

    if renderer == "parallel_podkill":
        _validate_parallel_podkill(case, target_ids)
    elif renderer == "podkill_then_network":
        _validate_podkill_then_network(case, target_ids)
    elif renderer == "network_then_parallel_podkill":
        _validate_network_then_parallel_podkill(case, target_ids)
    elif renderer == "network_parallel_containerkill":
        _validate_network_parallel_containerkill(case, target_ids)
    elif renderer in ("cpu_stress_parallel", "cpu_stress_single_role", "memory_stress_parallel", "memory_stress_single_role"):
        _validate_stress_renderer(case, target_ids)
    elif renderer == "modular_chaos":
        _validate_modular_case(case, target_ids)

    return case
