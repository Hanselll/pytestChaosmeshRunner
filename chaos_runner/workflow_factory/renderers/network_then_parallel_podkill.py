# -*- coding: utf-8 -*-
import random
import re
from chaos_runner.workflow_factory.renderers import register
from chaos_runner.workflow_factory.renderers.value_resolver import resolve_duration, resolve_percent

@register("network_then_parallel_podkill")
def render(case, resolved, config):
    wf = case.get("workflow") or {}
    wf_name = wf.get("name") or case.get("name") or "wf"
    wf_ns = wf.get("namespace") or config.WF_NAMESPACE
    ns = config.NS_TARGET

    # -------------------------
    # network config
    # -------------------------
    net = case.get("network") or {}
    net_deadline = int(net.get("deadline_seconds", net.get("duration", 180)))
    direction = net.get("direction", getattr(config, "NET_DIRECTION", "both"))

    selectors = net.get("selectors") or {}
    from_tid = selectors.get("from")
    to_tid = selectors.get("to")
    if not from_tid or not to_tid:
        raise RuntimeError("network.selectors.from/to is required, e.g. from: upc, to: rc")

    # labels are optional:
    # - If provided, use labelSelectors (useful when targets are selected by labels).
    # - If omitted, fall back to resolved pod names from network.selectors.from/to.
    labels = net.get("labels") or {}
    from_label = labels.get("from")
    to_label = labels.get("to")

    # ---------------------------------------------------------------------
    # Normalize label selector strings
    #
    # Case files may supply Kubernetes label selectors in either YAML style
    # ("key: value") or CLI style ("key=value"). Chaos Mesh expects a YAML
    # mapping under ``labelSelectors``, so we convert "key=value" into
    # "key: value". Quotes around the string are also stripped.
    def _normalize_label_str(s):
        if not isinstance(s, str):
            return s
        val = s.strip().strip('"').strip("'")
        # If the string contains an '=' but no ':' before it, treat as key=value.
        if '=' in val and (val.count(':') == 0 or val.find('=') < val.find(':')):
            k, v = [p.strip() for p in val.split('=', 1)]
            return "{}: {}".format(k, v)
        return val

    from_label = _normalize_label_str(from_label) if from_label else None
    to_label = _normalize_label_str(to_label) if to_label else None

    def _as_list(val):
        if val is None:
            return []
        if isinstance(val, dict):
            return [val]
        if isinstance(val, list):
            return val
        raise RuntimeError("unexpected resolved target type: {}".format(type(val)))

    def _pods_block(pods, indent_spaces):
        """Render a pods selector block with the given indentation."""
        prefix = " " * indent_spaces
        # pods:
        #   <ns>:
        #     - pod-a
        #     - pod-b
        pods_yaml = "\n".join([f"{prefix}    - {p}" for p in pods])
        return (
            f"{prefix}pods:\n"
            f"{prefix}  {ns}:\n"
            f"{pods_yaml}"
        )

    # Determine whether we can use resolved pod names as selector/target.
    from_resolved = _as_list(resolved.get(from_tid))
    to_resolved = _as_list(resolved.get(to_tid))
    from_pods = [x.get("pod") for x in from_resolved if isinstance(x, dict) and x.get("pod")]
    to_pods = [x.get("pod") for x in to_resolved if isinstance(x, dict) and x.get("pod")]

    # Build selector YAML snippet (under networkChaos) - either labelSelectors or pods.
    if from_label:
        selector_extra = """          labelSelectors:\n            {from_label}""".format(from_label=from_label)
    else:
        if not from_pods:
            raise RuntimeError(
                "cannot determine network selector: provide network.labels.from or ensure selectors.from resolves to pods"
            )
        selector_extra = _pods_block(from_pods, indent_spaces=10)

    # Build target YAML snippet (under networkChaos.target.selector) - either labelSelectors or pods.
    if to_label:
        target_selector_extra = """            labelSelectors:\n              {to_label}""".format(to_label=to_label)
    else:
        if not to_pods:
            raise RuntimeError(
                "cannot determine network target: provide network.labels.to or ensure selectors.to resolves to pods"
            )
        target_selector_extra = _pods_block(to_pods, indent_spaces=12)

    # 支持同时 delay + loss (+ partition)
    # 用法：
    #   network:
    #     actions: [delay, loss]
    # 或  network:
    #     action: both
    actions = net.get("actions")
    if actions is None:
        a = (net.get("action") or "delay").lower()
        if a in ("both", "delay+loss", "loss+delay"):
            actions = ["delay", "loss"]
        else:
            actions = [a]

    delay_cfg = net.get("delay") or {}
    loss_cfg = net.get("loss") or {}

    net_children = []
    net_templates = []

    if "delay" in actions:
        lat = resolve_duration(delay_cfg.get("latency"), field_name="network.delay.latency", default=getattr(config, "NET_DELAY", "100ms"))
        jit = resolve_duration(delay_cfg.get("jitter"), field_name="network.delay.jitter", default=getattr(config, "NET_JITTER", "10ms"))
        net_children.append("net-delay")
        net_templates.append("""
    - name: net-delay
      templateType: NetworkChaos
      deadline: {net_deadline}s
      networkChaos:
        action: delay
        mode: all
        selector:
          namespaces:
            - {ns}
{selector_extra}
        direction: {direction}
        target:
          mode: all
          selector:
            namespaces:
              - {ns}
{target_selector_extra}
        delay:
          latency: "{lat}"
          jitter: "{jit}"
""".format(
            net_deadline=net_deadline,
            ns=ns,
            selector_extra=selector_extra,
            target_selector_extra=target_selector_extra,
            direction=direction,
            lat=lat,
            jit=jit,
        ))

    if "loss" in actions:
        loss = resolve_percent(loss_cfg.get("loss"), field_name="network.loss.loss", default=getattr(config, "NET_LOSS", "1"))
        corr = str(loss_cfg.get("correlation", getattr(config, "NET_CORR", "0")))
        net_children.append("net-loss")
        net_templates.append("""
    - name: net-loss
      templateType: NetworkChaos
      deadline: {net_deadline}s
      networkChaos:
        action: loss
        mode: all
        selector:
          namespaces:
            - {ns}
{selector_extra}
        direction: {direction}
        target:
          mode: all
          selector:
            namespaces:
              - {ns}
{target_selector_extra}
        loss:
          loss: "{loss}"
          correlation: "{corr}"
""".format(
            net_deadline=net_deadline,
            ns=ns,
            selector_extra=selector_extra,
            target_selector_extra=target_selector_extra,
            direction=direction,
            loss=loss,
            corr=corr,
        ))

    if "partition" in actions:
        net_children.append("net-partition")
        net_templates.append("""
    - name: net-partition
      templateType: NetworkChaos
      deadline: {net_deadline}s
      networkChaos:
        action: partition
        mode: all
        selector:
          namespaces:
            - {ns}
{selector_extra}
        direction: {direction}
        target:
          mode: all
          selector:
            namespaces:
              - {ns}
{target_selector_extra}
""".format(
            net_deadline=net_deadline,
            ns=ns,
            selector_extra=selector_extra,
            target_selector_extra=target_selector_extra,
            direction=direction,
        ))

    if not net_children:
        raise RuntimeError("network.actions/action produced empty list (supported: delay, loss, partition, both)")

    # -------------------------
    # kill plan (支持 expand)
    # -------------------------
    kill = case.get("kill") or {}
    items = kill.get("items") or []
    if not items:
        raise RuntimeError("kill.items is empty")

    plan = []  # [{pod, delay}, ...]

    def _is_zero_delay(d):
        """
        Return True if the delay string represents zero time.
        """
        s = str(d).strip().lower()
        if s in ("0", "0s", "0ms", "0us", "0ns", "0m", "0h"):
            return True
        m = re.match(r"^(\d+(?:\.\d+)?)(ns|us|ms|s|m|h)$", s)
        if m:
            try:
                return float(m.group(1)) == 0.0
            except ValueError:
                return False
        return False

    def _expand_list_target(tid, val_list, expand_cfg):
        if expand_cfg is None:
            raise RuntimeError("target {} is list; please set expand (all / random+count / indices)".format(tid))

        if expand_cfg == "all":
            return val_list

        if isinstance(expand_cfg, dict):
            if "indices" in expand_cfg:
                idxs = expand_cfg.get("indices") or []
                if not isinstance(idxs, list) or not idxs:
                    raise RuntimeError("expand.indices must be a non-empty list for target {}".format(tid))
                out = []
                for i in idxs:
                    if not isinstance(i, int):
                        raise RuntimeError("expand.indices must be int list for target {}".format(tid))
                    if i < 0 or i >= len(val_list):
                        raise RuntimeError("expand.indices {} out of range (len={}) for target {}".format(i, len(val_list), tid))
                    out.append(val_list[i])
                return out

            mode = (expand_cfg.get("mode") or "").lower()
            if mode == "random":
                count = int(expand_cfg.get("count", 1))
                if count < 1:
                    raise RuntimeError("expand.count must be >=1 for target {}".format(tid))
                if count > len(val_list):
                    raise RuntimeError("expand.count={} > available {} for target {}".format(count, len(val_list), tid))
                seed = expand_cfg.get("seed", None)
                if seed is not None:
                    random.seed(seed)
                return random.sample(val_list, count)

        raise RuntimeError("invalid expand for target {}: {}".format(tid, expand_cfg))

    for it in items:
        tid = it["target"]
        raw_delay = it.get("delay", 0)
        if raw_delay is None or raw_delay == "":
            raise RuntimeError("delay is empty for target {}".format(tid))
        delay_sec = resolve_duration(raw_delay, field_name="kill.items.delay")

        expand_cfg = it.get("expand")
        val = resolved.get(tid)
        if val is None:
            raise RuntimeError("unknown target id: {}".format(tid))

        if isinstance(val, dict):
            plan.append({"pod": val["pod"], "delay": delay_sec})
            continue

        if isinstance(val, list):
            selected = _expand_list_target(tid, val, expand_cfg)
            for x in selected:
                plan.append({"pod": x["pod"], "delay": delay_sec})
            continue

        raise RuntimeError("unsupported target type: {} for {}".format(type(val), tid))

    # -------------------------
    # templates: 顶层 Parallel
    #   - net-parallel（delay/loss 并行）  [180s]
    #   - kill-parallel（每个 pod 一个 branch 串行 wait->kill）[瞬时]
    # -------------------------
    branches = []
    kill_templates = []

    for i, it in enumerate(plan):
        pod = it["pod"]
        delay_sec = it["delay"]

        b = "branch-{}".format(i)
        branches.append(b)

        if not _is_zero_delay(delay_sec):
            kill_templates.append("""
    - name: wait-{i}
      templateType: Suspend
      deadline: {d}
""".format(i=i, d=delay_sec))
            children = "\n        - ".join(["wait-{}".format(i), "kill-{}".format(i)])
        else:
            children = "kill-{}".format(i)

        kill_templates.append("""
    - name: {b}
      templateType: Serial
      children:
        - {children}

    - name: kill-{i}
      templateType: PodChaos
      podChaos:
        action: pod-kill
        selector:
          namespaces:
            - {ns}
          pods:
            {ns}:
              - {pod}
        mode: all
""".format(b=b, children=children, i=i, ns=ns, pod=pod))

    return """apiVersion: chaos-mesh.org/v1alpha1
kind: Workflow
metadata:
  name: "{wf}"
  namespace: "{wfn}"
spec:
  entry: {wf}
  templates:
    - name: {wf}
      templateType: Parallel
      children:
        - net-parallel
        - kill-parallel

    - name: net-parallel
      templateType: Parallel
      children:
        - {net_children}

{net_templates}

    - name: kill-parallel
      templateType: Parallel
      children:
        - {branches}

{kill_templates}
""".format(
        wf=wf_name,
        wfn=wf_ns,
        net_children="\n        - ".join(net_children),
        net_templates="".join(net_templates).rstrip("\n"),
        branches="\n        - ".join(branches),
        kill_templates="".join(kill_templates).rstrip("\n")
    )

