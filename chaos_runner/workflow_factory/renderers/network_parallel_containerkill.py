# -*- coding: utf-8 -*-
import random
from chaos_runner.workflow_factory.renderers import register
from chaos_runner.workflow_factory.renderers.value_resolver import resolve_duration, resolve_percent


def _format_delay(d):
    return resolve_duration(d, field_name="kill.items.delay")


def _is_zero_delay(delay_str: str) -> bool:
    s = (delay_str or "").strip()
    return s in ("0", "0s", "0ms", "0m", "0h")


def _kv(label_kv: str):
    if ":" not in label_kv:
        raise RuntimeError(f"invalid label format: {label_kv}, expected 'key: value'")
    k, v = [x.strip() for x in label_kv.split(":", 1)]
    return k, v


def _select_pods(val, expand_cfg):
    """
    val: dict or list[dict], each dict like {"pod": "...", "ip": "..."}
    expand_cfg:
      mode: all|random|indices
      count: N (random)
      indices: [...]
    """
    if isinstance(val, dict):
        pods = [val]
    elif isinstance(val, list):
        pods = val[:]
    else:
        raise RuntimeError(f"unexpected resolved target type: {type(val)}")

    if not expand_cfg:
        return pods

    mode = (expand_cfg.get("mode") or "all").lower()
    if mode == "random":
        count = int(expand_cfg.get("count", 1))
        if count > len(pods):
            count = len(pods)
        return random.sample(pods, count)
    if mode == "indices":
        idxs = expand_cfg.get("indices") or []
        return [pods[i] for i in idxs if 0 <= i < len(pods)]
    # all
    return pods


@register("network_parallel_containerkill")
def render(case, resolved, config):
    wf = case.get("workflow") or {}
    wf_name = wf.get("name") or case.get("name") or "wf"
    wf_ns = wf.get("namespace") or config.WF_NAMESPACE
    ns_target = config.NS_TARGET

    # ------------------------
    # NetworkChaos：支持并行 delay/loss 风格，与 network_then_parallel_podkill 一致
    # ------------------------
    net = case.get("network") or {}
    # deadline_seconds takes priority over duration to align with the podkill renderer
    net_deadline = int(net.get("deadline_seconds", net.get("duration", 180)))
    # direction of network chaos, fallback to config.NET_DIRECTION or "both"
    direction = net.get("direction", getattr(config, "NET_DIRECTION", "both"))

    # selectors: network.selectors.from/to 指向 case.targets 中的 id；必须提供
    selectors = net.get("selectors") or {}
    from_tid = selectors.get("from")
    to_tid = selectors.get("to")
    if not from_tid or not to_tid:
        raise RuntimeError("network.selectors.from/to is required, e.g. from: upc, to: rc")

    # labels: optional K/V for labelSelectors. If omitted, we fall back to
    # resolved pod lists referenced by network.selectors.from/to.
    labels = net.get("labels") or {}
    from_label = labels.get("from")
    to_label = labels.get("to")

    # ---------------------------------------------------------------------
    # Selector building: labelSelectors (map) OR pods (explicit names)
    #
    # Chaos Mesh NetworkChaos supports selecting pods either by labels
    # (labelSelectors) or by explicit pod names (pods). Partitioning Sentinel
    # <-> current master is most accurately expressed using explicit pod names,
    # because master/slave pods usually share the same labels.
    #
    # If network.labels.from/to are present, we use labelSelectors.
    # If absent, we use resolved targets from selectors.from/to.
    def _normalize_label_str(s: str) -> str:
        if not isinstance(s, str):
            return s
        val = s.strip().strip('"').strip("'")
        if '=' in val and (val.count(':') == 0 or val.find('=') < val.find(':')):
            k, v = [p.strip() for p in val.split('=', 1)]
            return f"{k}: {v}"
        return val

    def _label_clause(label_line: str, value_indent: int) -> str:
        # label_line is single line like "k: v"
        return "labelSelectors:\n" + (" " * value_indent) + label_line

    def _pods_clause(val, ns: str, ns_indent: int, item_indent: int) -> str:
        if isinstance(val, dict):
            pods = [val.get("pod")]
        elif isinstance(val, list):
            pods = [x.get("pod") for x in val]
        else:
            raise RuntimeError(f"unexpected resolved target type for pods selector: {type(val)}")
        pods = [p for p in pods if p]
        if not pods:
            raise RuntimeError("pods selector got empty pod list")
        lines = ["pods:", (" " * ns_indent) + f"{ns}:"]
        for p in pods:
            lines.append((" " * item_indent) + f"- {p}")
        return "\n".join(lines)

    use_labels = bool(from_label and to_label)
    if use_labels:
        from_label = _normalize_label_str(from_label)
        to_label = _normalize_label_str(to_label)
        from_selector_clause = _label_clause(from_label, value_indent=10)
        to_selector_clause = _label_clause(to_label, value_indent=12)
    else:
        from_val = resolved.get(from_tid)
        to_val = resolved.get(to_tid)
        if from_val is None or to_val is None:
            raise RuntimeError(
                "network.labels.from/to not set, and cannot find resolved targets for network.selectors.from/to"
            )
        # selector block lives under 8-space indent; values at 10/12.
        from_selector_clause = _pods_clause(from_val, ns_target, ns_indent=10, item_indent=12)
        # target.selector block lives under 10-space indent; values at 12/14.
        to_selector_clause = _pods_clause(to_val, ns_target, ns_indent=12, item_indent=14)

    # actions: allow multiple actions.
    #
    # Supported values:
    #   - delay
    #   - loss
    #   - partition
    #   - both / delay+loss / loss+delay  (shorthand)
    actions = net.get("actions")
    if actions is None:
        a = (net.get("action") or "delay").lower()
        if a in ("both", "delay+loss", "loss+delay"):
            actions = ["delay", "loss"]
        else:
            actions = [a]

    delay_cfg = net.get("delay") or {}
    loss_cfg = net.get("loss") or {}

    # build network children and templates
    net_children = []
    net_templates = []

    if "delay" in actions:
        # latency and jitter from case or config defaults
        lat = resolve_duration(delay_cfg.get("latency"), field_name="network.delay.latency", default=getattr(config, "NET_DELAY", "100ms"))
        jit = resolve_duration(delay_cfg.get("jitter"), field_name="network.delay.jitter", default=getattr(config, "NET_JITTER", "10ms"))
        net_children.append("net-delay")
        net_templates.append(f"""
  - name: net-delay
    templateType: NetworkChaos
    deadline: {net_deadline}s
    networkChaos:
      action: delay
      mode: all
      selector:
        namespaces:
          - {ns_target}
        {from_selector_clause}
      direction: {direction}
      target:
        mode: all
        selector:
          namespaces:
            - {ns_target}
          {to_selector_clause}
      delay:
        latency: "{lat}"
        jitter: "{jit}"
""")

    if "loss" in actions:
        # loss and correlation from case or config defaults
        loss = resolve_percent(loss_cfg.get("loss"), field_name="network.loss.loss", default=getattr(config, "NET_LOSS", "1"))
        corr = str(loss_cfg.get("correlation", getattr(config, "NET_CORR", "0")))
        net_children.append("net-loss")
        net_templates.append(f"""
  - name: net-loss
    templateType: NetworkChaos
    deadline: {net_deadline}s
    networkChaos:
      action: loss
      mode: all
      selector:
        namespaces:
          - {ns_target}
        {from_selector_clause}
      direction: {direction}
      target:
        mode: all
        selector:
          namespaces:
            - {ns_target}
          {to_selector_clause}
      loss:
        loss: "{loss}"
        correlation: "{corr}"
""")

    if "partition" in actions:
        # Network partition between selector pods and target pods.
        # NOTE: partition has no delay/loss config.
        net_children.append("net-partition")
        net_templates.append(f"""
  - name: net-partition
    templateType: NetworkChaos
    deadline: {net_deadline}s
    networkChaos:
      action: partition
      mode: all
      selector:
        namespaces:
          - {ns_target}
        {from_selector_clause}
      direction: {direction}
      target:
        mode: all
        selector:
          namespaces:
            - {ns_target}
          {to_selector_clause}
""")

    if not net_children:
        raise RuntimeError(
            "network.actions/action produced empty list (supported: delay, loss, partition, both)"
        )

    net_parallel_name = "net-parallel"

    # ------------------------
    # ContainerKill（PodChaos.action=container-kill）
    # 且：不同 Pod 的 kill 必须 parallel，并允许每个 Pod 单独 delay
    # ------------------------
    kill = case.get("kill") or {}
    items = kill.get("items") or []
    default_container_names = kill.get("containerNames") or []

    # 生成 “pod 级别的任务” 列表：每个任务 = 一个 Pod 的一次 container-kill（含 delay + containerNames）
    tasks = []  # [{pod, delay, containerNames}, ...]

    for it in items:
        tid = it["target"]
        delay_str = _format_delay(it.get("delay", 0))
        expand_cfg = it.get("expand")

        # 这三个层级的容器名优先级：
        # containerMap[pod] > item.containerNames > kill.containerNames
        container_map = it.get("containerMap") or {}
        item_cnames = it.get("containerNames") or None

        val = resolved.get(tid)
        if val is None:
            raise RuntimeError(f"unknown target id: {tid}")

        pods = _select_pods(val, expand_cfg)

        for p in pods:
            pod_name = p["pod"]
            cnames = container_map.get(pod_name) or item_cnames or default_container_names
            if not cnames:
                raise RuntimeError(
                    f"containerNames required for pod={pod_name} target={tid}. "
                    f"Provide kill.items[].containerMap[{pod_name}] or kill.items[].containerNames or kill.containerNames."
                )
            tasks.append({"pod": pod_name, "delay": delay_str, "containerNames": cnames})

    # ------------------------
    # 构建 kill-parallel：每个 Pod 一个 branch，并行执行
    # branch: Serial( Suspend(delay)?, PodChaos(container-kill) )
    # ------------------------
    kill_parallel_name = "kill-parallel"
    # Collect kill templates and branch names separately
    kill_templates = []
    branch_names = []
    for i, t in enumerate(tasks):
        pod = t["pod"]
        delay_str = t["delay"]
        cnames = t["containerNames"]

        # template names
        chaos_name = f"ck-{i}"
        wait_name = f"wait-{i}"
        branch_name = f"b-{i}"

        cn_list = "\n        - ".join(cnames)

        # PodChaos(container-kill)
        kill_templates.append(f"""
  - name: {chaos_name}
    templateType: PodChaos
    podChaos:
      action: container-kill
      mode: one
      selector:
        namespaces:
          - {ns_target}
        pods:
          {ns_target}:
            - {pod}
      containerNames:
        - {cn_list}
""")

        if _is_zero_delay(delay_str):
            # branch directly points to chaos
            branch_names.append(chaos_name)
        else:
            # first wait then kill
            kill_templates.append(f"""
  - name: {wait_name}
    templateType: Suspend
    deadline: {delay_str}
""")
            kill_templates.append(f"""
  - name: {branch_name}
    templateType: Serial
    children:
      - {wait_name}
      - {chaos_name}
""")
            branch_names.append(branch_name)

    branches_yaml = "\n      - ".join(branch_names) if branch_names else ""
    kill_parallel_tpl = f"""
  - name: {kill_parallel_name}
    templateType: Parallel
    children:
      - {branches_yaml}
""" if branch_names else f"""
  - name: {kill_parallel_name}
    templateType: Parallel
    children: []
"""

    # ------------------------
    # Root：Parallel(network, kill-parallel)
    # ------------------------
    # assemble net-parallel template and network children
    # Each child in the children list must start with a '-' on its own line; build the YAML accordingly
    net_children_yaml = "\n      - ".join(net_children)
    net_parallel_tpl = f"""
  - name: {net_parallel_name}
    templateType: Parallel
    children:
      - {net_children_yaml}
"""

    # combine network and kill templates into single strings
    net_templates_str = "".join(net_templates).rstrip("\n")
    kill_templates_str = "".join(kill_templates).rstrip("\n")

    root = f"""
apiVersion: chaos-mesh.org/v1alpha1
kind: Workflow
metadata:
  name: {wf_name}
  namespace: {wf_ns}
spec:
  entry: entry
  templates:
  - name: entry
    templateType: Parallel
    children:
      - {net_parallel_name}
      - {kill_parallel_name}
{net_parallel_tpl}
{net_templates_str}
{kill_parallel_tpl}
{kill_templates_str}
"""
    return root.strip() + "\n"


