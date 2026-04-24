# -*- coding: utf-8 -*-
import random

from chaos_runner.workflow_factory.renderers import register
from chaos_runner.workflow_factory.renderers.value_resolver import resolve_duration, resolve_percent

FAULT_BUILDERS = {}


def fault_builder(name):
    def deco(fn):
        FAULT_BUILDERS[name] = fn
        return fn
    return deco


def _pick_targets(resolved, target_id, expand=None):
    val = resolved.get(target_id)
    if val is None:
        known = ", ".join(sorted([str(k) for k in resolved.keys()]))
        raise RuntimeError("unknown target id: {} (known targets: {})".format(target_id, known or "<none>"))
    if isinstance(val, dict):
        return [val]
    if not isinstance(val, list):
        raise RuntimeError("unsupported target type for {}: {}".format(target_id, type(val)))
    if expand == "all":
        return val
    if isinstance(expand, dict):
        if "indices" in expand:
            out = []
            for i in expand.get("indices") or []:
                if not isinstance(i, int) or i < 0 or i >= len(val):
                    raise RuntimeError("expand.indices out of range for {}".format(target_id))
                out.append(val[i])
            if not out:
                raise RuntimeError("expand.indices empty for {}".format(target_id))
            return out
        if (expand.get("mode") or "").lower() == "random":
            count = int(expand.get("count", 1))
            if count < 1 or count > len(val):
                raise RuntimeError("expand.count invalid for {}".format(target_id))
            seed = expand.get("seed")
            if seed is not None:
                random.seed(seed)
            return random.sample(val, count)
    raise RuntimeError("target {} is list, set expand (all/random/indices)".format(target_id))


def _dns_name(name):
    return str(name).replace("_", "-").lower()


def _pods_block(ns, pods, ns_indent, item_indent):
    lines = [(" " * ns_indent) + "{}:".format(ns)]
    for p in pods:
        lines.append((" " * item_indent) + "- {}".format(p))
    return "\n".join(lines)


def _normalize_label_str(value):
    if not isinstance(value, str):
        raise RuntimeError("label selector must be a string, got {}".format(type(value)))
    text = value.strip().strip('"').strip("'")
    if "=" in text and (":" not in text or text.find("=") < text.find(":")):
        key, val = [x.strip() for x in text.split("=", 1)]
        return "{}: {}".format(key, val)
    return text


def _selector_block(ns, indent_spaces, pods=None, label=None):
    prefix = " " * indent_spaces
    if label is not None:
        return "{}labelSelectors:\n{}  {}".format(prefix, prefix, _normalize_label_str(label))
    pods = [p for p in (pods or []) if p]
    if not pods:
        raise RuntimeError("pods selector resolved to empty list")
    lines = ["{}pods:".format(prefix), "{}  {}:".format(prefix, ns)]
    for pod in pods:
        lines.append("{}    - {}".format(prefix, pod))
    return "\n".join(lines)


def _resolve_network_endpoint(ctx, fault, side, ns, indent_spaces):
    labels = fault.get("labels") or {}
    label = labels.get(side)
    if label:
        return _selector_block(ns, indent_spaces, label=label)

    selectors = fault.get("selectors") or {}
    target_id = selectors.get(side)
    if not target_id:
        raise RuntimeError("network fault requires labels.{0} or selectors.{0}".format(side))
    expand = selectors.get("{}_expand".format(side), "all")
    pods = [x.get("pod") for x in _pick_targets(ctx["resolved"], target_id, expand) if x.get("pod")]
    return _selector_block(ns, indent_spaces, pods=pods)


def _with_optional_delay(ctx, fault_name, action_tpl, delay_val):
    if delay_val is None:
        return fault_name, [action_tpl]
    d = resolve_duration(delay_val, field_name="fault.delay", default="0s")
    if d in ("0s", "0ms", "0"):
        return fault_name, [action_tpl]
    wait_name = "{}-wait".format(fault_name)
    serial_name = "{}-serial".format(fault_name)
    wait_tpl = """
    - name: {name}
      templateType: Suspend
      deadline: {deadline}
""".format(name=wait_name, deadline=d)
    serial_tpl = """
    - name: {name}
      templateType: Serial
      children:
        - {wait}
        - {act}
""".format(name=serial_name, wait=wait_name, act=fault_name)
    return serial_name, [wait_tpl, action_tpl, serial_tpl]


@fault_builder("pod_kill")
def build_pod_kill(ctx, fault, idx):
    ns = ctx["ns"]
    target = fault.get("target")
    pods = [x.get("pod") for x in _pick_targets(ctx["resolved"], target, fault.get("expand")) if x.get("pod")]
    # PodChaos without deadline can keep running and block later serial stages.
    # Keep a short default so this remains a trigger-style kill action.
    deadline = resolve_duration(fault.get("duration", fault.get("deadline", "1s")), field_name="fault.duration", default="1s")
    children = []
    templates = []
    for i, pod in enumerate(pods):
        name = "f{}-podkill-{}".format(idx, i)
        action = """
    - name: {name}
      templateType: PodChaos
      deadline: {deadline}
      podChaos:
        action: pod-kill
        mode: all
        selector:
          namespaces:
            - {ns}
          pods:
{pods}
""".format(name=name, deadline=deadline, ns=ns, pods=_pods_block(ns, [pod], 12, 14))
        root, tpls = _with_optional_delay(ctx, name, action, fault.get("delay"))
        children.append(root)
        templates.extend(tpls)
    return _fanout(ctx, "f{}-podkill-root".format(idx), children, templates)


@fault_builder("container_kill")
def build_container_kill(ctx, fault, idx):
    ns = ctx["ns"]
    target = fault.get("target")
    default_cnames = fault.get("containerNames") or []
    container_map = fault.get("containerMap") or {}
    if not default_cnames and not container_map:
        raise RuntimeError("container_kill.containerNames required")
    pods = [x.get("pod") for x in _pick_targets(ctx["resolved"], target, fault.get("expand")) if x.get("pod")]
    # PodChaos without deadline can keep running and block later serial stages.
    # Keep a short default so this remains a trigger-style kill action.
    deadline = resolve_duration(fault.get("duration", fault.get("deadline", "1s")), field_name="fault.duration", default="1s")
    children, templates = [], []
    for i, pod in enumerate(pods):
        cnames = container_map.get(pod) or default_cnames
        if not cnames:
            raise RuntimeError("container_kill missing containerNames for pod {}".format(pod))
        name = "f{}-ctrkill-{}".format(idx, i)
        action = """
    - name: {name}
      templateType: PodChaos
      deadline: {deadline}
      podChaos:
        action: container-kill
        mode: one
        selector:
          namespaces:
            - {ns}
          pods:
{pods}
        containerNames:
{cnames}
""".format(
            name=name,
            deadline=deadline,
            ns=ns,
            pods=_pods_block(ns, [pod], 12, 14),
            cnames="\n".join(["          - {}".format(c) for c in cnames]),
        )
        root, tpls = _with_optional_delay(ctx, name, action, fault.get("delay"))
        children.append(root)
        templates.extend(tpls)
    return _fanout(ctx, "f{}-ctrkill-root".format(idx), children, templates)


def _network_common(ctx, fault):
    ns = ctx["ns"]
    from_selector = _resolve_network_endpoint(ctx, fault, "from", ns, indent_spaces=10)
    to_selector = _resolve_network_endpoint(ctx, fault, "to", ns, indent_spaces=12)
    direction = fault.get("direction", "both")
    deadline = resolve_duration(fault.get("duration", fault.get("deadline", "60s")), field_name="fault.duration", default="60s")
    return ns, from_selector, to_selector, direction, deadline


@fault_builder("network_delay")
def build_network_delay(ctx, fault, idx):
    ns, from_selector, to_selector, direction, deadline = _network_common(ctx, fault)
    lat = resolve_duration((fault.get("delay") or {}).get("latency"), field_name="network.delay.latency", default="100ms")
    jit = resolve_duration((fault.get("delay") or {}).get("jitter"), field_name="network.delay.jitter", default="10ms")
    name = "f{}-net-delay".format(idx)
    tpl = """
    - name: {name}
      templateType: NetworkChaos
      deadline: {deadline}
      networkChaos:
        action: delay
        mode: all
        selector:
          namespaces:
            - {ns}
{from_selector}
        direction: {direction}
        target:
          mode: all
          selector:
            namespaces:
              - {ns}
{to_selector}
        delay:
          latency: "{lat}"
          jitter: "{jit}"
""".format(name=name, deadline=deadline, ns=ns, from_selector=from_selector, direction=direction, to_selector=to_selector, lat=lat, jit=jit)
    return name, [tpl]


@fault_builder("network_loss")
def build_network_loss(ctx, fault, idx):
    ns, from_selector, to_selector, direction, deadline = _network_common(ctx, fault)
    loss = resolve_percent((fault.get("loss") or {}).get("loss"), field_name="network.loss.loss", default="1")
    corr = str((fault.get("loss") or {}).get("correlation", "0"))
    name = "f{}-net-loss".format(idx)
    tpl = """
    - name: {name}
      templateType: NetworkChaos
      deadline: {deadline}
      networkChaos:
        action: loss
        mode: all
        selector:
          namespaces:
            - {ns}
{from_selector}
        direction: {direction}
        target:
          mode: all
          selector:
            namespaces:
              - {ns}
{to_selector}
        loss:
          loss: "{loss}"
          correlation: "{corr}"
""".format(name=name, deadline=deadline, ns=ns, from_selector=from_selector, direction=direction, to_selector=to_selector, loss=loss, corr=corr)
    return name, [tpl]


@fault_builder("network_partition")
def build_network_partition(ctx, fault, idx):
    ns, from_selector, to_selector, direction, deadline = _network_common(ctx, fault)
    name = "f{}-net-partition".format(idx)
    tpl = """
    - name: {name}
      templateType: NetworkChaos
      deadline: {deadline}
      networkChaos:
        action: partition
        mode: all
        selector:
          namespaces:
            - {ns}
{from_selector}
        direction: {direction}
        target:
          mode: all
          selector:
            namespaces:
              - {ns}
{to_selector}
""".format(name=name, deadline=deadline, ns=ns, from_selector=from_selector, direction=direction, to_selector=to_selector)
    return name, [tpl]


def _fanout(ctx, root_name, children, templates):
    if len(children) == 1:
        return children[0], templates
    tpl = """
    - name: {name}
      templateType: Parallel
      children:
        - {children}
""".format(name=root_name, children="\n        - ".join(children))
    return root_name, templates + [tpl]


def _build_stress(ctx, fault, idx, mode):
    ns = ctx["ns"]
    pods = [x.get("pod") for x in _pick_targets(ctx["resolved"], fault.get("target"), fault.get("expand")) if x.get("pod")]
    deadline = resolve_duration(fault.get("duration", "30s"), field_name="stress.duration", default="30s")
    name = _dns_name("f{}-{}".format(idx, mode))
    if mode == "cpu-stress":
        cfg = fault.get("cpu") or {}
        stressor = """          cpu:\n            workers: {workers}\n            load: {load}""".format(
            workers=int(cfg.get("workers", 1)), load=int(cfg.get("load", 80))
        )
    else:
        cfg = fault.get("memory") or {}
        stressor = """          memory:\n            workers: {workers}\n            size: \"{size}\"""".format(
            workers=int(cfg.get("workers", 1)), size=str(cfg.get("size", "256MB"))
        )
    tpl = """
    - name: {name}
      templateType: StressChaos
      deadline: {deadline}
      stressChaos:
        mode: all
        selector:
          namespaces:
            - {ns}
          pods:
{pods}
        stressors:
{stressor}
""".format(name=name, deadline=deadline, ns=ns, pods=_pods_block(ns, pods, 12, 14), stressor=stressor)
    return name, [tpl]


@fault_builder("cpu_stress")
def build_cpu_stress(ctx, fault, idx):
    return _build_stress(ctx, fault, idx, "cpu-stress")


@fault_builder("memory_stress")
def build_memory_stress(ctx, fault, idx):
    return _build_stress(ctx, fault, idx, "memory-stress")


def _build_group(group_name, mode, children):
    return """
    - name: {name}
      templateType: {tt}
      children:
        - {children}
""".format(name=group_name, tt=("Parallel" if mode == "parallel" else "Serial"), children="\n        - ".join(children))


@register("modular_chaos")
def render(case, resolved, config):
    wf = case.get("workflow") or {}
    wf_name = wf.get("name") or case.get("name") or "wf"
    wf_ns = wf.get("namespace") or config.WF_NAMESPACE
    ctx = {"resolved": resolved, "ns": config.NS_TARGET}

    stages = case.get("stages")
    templates = []
    top_children = []

    if stages:
        for si, stage in enumerate(stages):
            faults = stage.get("faults") or []
            if not faults:
                continue
            stage_children = []
            for fi, f in enumerate(faults):
                ftype = f.get("type")
                if ftype not in FAULT_BUILDERS:
                    raise RuntimeError("unknown fault type: {} (supported={})".format(ftype, sorted(FAULT_BUILDERS.keys())))
                root, tpls = FAULT_BUILDERS[ftype](ctx, f, si * 100 + fi)
                stage_children.append(root)
                templates.extend(tpls)
            sname = "stage-{}".format(si)
            templates.append(_build_group(sname, stage.get("mode", "parallel"), stage_children))
            top_children.append(sname)
        top_mode = "Serial"
    else:
        faults = case.get("faults") or []
        if not faults:
            raise RuntimeError("modular_chaos requires 'faults' or 'stages'")
        for i, f in enumerate(faults):
            ftype = f.get("type")
            if ftype not in FAULT_BUILDERS:
                raise RuntimeError("unknown fault type: {} (supported={})".format(ftype, sorted(FAULT_BUILDERS.keys())))
            root, tpls = FAULT_BUILDERS[ftype](ctx, f, i)
            top_children.append(root)
            templates.extend(tpls)
        top_mode = "Parallel" if (case.get("mode", "parallel").lower() == "parallel") else "Serial"

    return """apiVersion: chaos-mesh.org/v1alpha1
kind: Workflow
metadata:
  name: \"{wf}\"
  namespace: \"{wfn}\"
spec:
  entry: {wf}
  templates:
    - name: {wf}
      templateType: {top_mode}
      children:
        - {children}
{templates}
""".format(
        wf=wf_name,
        wfn=wf_ns,
        top_mode=top_mode,
        children="\n        - ".join(top_children),
        templates="".join(templates).rstrip("\n"),
    )
