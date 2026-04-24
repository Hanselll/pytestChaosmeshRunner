# -*- coding: utf-8 -*-
from chaos_runner.workflow_factory.renderers import register
from chaos_runner.workflow_factory.renderers.value_resolver import resolve_duration, resolve_percent

@register("podkill_then_network")
def render(case, resolved, config):
    wf = case.get("workflow") or {}
    wf_name = wf.get("name") or case.get("name") or "wf"
    wf_ns = wf.get("namespace") or config.WF_NAMESPACE
    ns = config.NS_TARGET

    kill = case.get("kill") or {}
    kill_targets = kill.get("targets") or []
    if len(kill_targets) < 2:
        raise RuntimeError("kill.targets must include >=2 targets (e.g. [upc, rc])")

    upc = resolved[kill_targets[0]]["pod"]
    rc  = resolved[kill_targets[1]]["pod"]

    net = case.get("network") or {}
    deadline = int(net.get("deadline_sec", 60))
    direction = net.get("direction", "both")
    upc_label = net.get("upc_label_kv", "app.kubernetes.io/component: dupf-pod-upc")
    rc_label  = net.get("rc_label_kv",  "app.kubernetes.io/component: dupf-registry-center")
    lat = resolve_duration(net.get("latency"), field_name="network.latency", default="100ms")
    jit = resolve_duration(net.get("jitter"), field_name="network.jitter", default="10ms")
    loss = resolve_percent(net.get("loss"), field_name="network.loss", default="1")
    corr = net.get("corr", "0")

    return """apiVersion: chaos-mesh.org/v1alpha1
kind: Workflow
metadata:
  name: "{wf}"
  namespace: "{wfn}"
spec:
  entry: {wf}
  templates:
    - name: {wf}
      templateType: Serial
      children:
        - kill-parallel
        - net-impair-parallel

    - name: kill-parallel
      templateType: Parallel
      children:
        - upc-talker-kill
        - rc-leader-kill

    - name: upc-talker-kill
      templateType: PodChaos
      podChaos:
        action: pod-kill
        selector:
          namespaces:
            - {ns}
          pods:
            {ns}:
              - {upc}
        mode: all

    - name: rc-leader-kill
      templateType: PodChaos
      podChaos:
        action: pod-kill
        selector:
          namespaces:
            - {ns}
          pods:
            {ns}:
              - {rc}
        mode: all

    - name: net-impair-parallel
      templateType: Parallel
      children:
        - net-delay
        - net-loss

    - name: net-delay
      templateType: NetworkChaos
      deadline: {deadline}s
      networkChaos:
        action: delay
        mode: all
        selector:
          namespaces:
            - {ns}
          labelSelectors:
            {upc_label}
        direction: {direction}
        target:
          mode: all
          selector:
            namespaces:
              - {ns}
            labelSelectors:
              {rc_label}
        delay:
          latency: "{lat}"
          jitter: "{jit}"

    - name: net-loss
      templateType: NetworkChaos
      deadline: {deadline}s
      networkChaos:
        action: loss
        mode: all
        selector:
          namespaces:
            - {ns}
          labelSelectors:
            {upc_label}
        direction: {direction}
        target:
          mode: all
          selector:
            namespaces:
              - {ns}
            labelSelectors:
              {rc_label}
        loss:
          loss: "{loss}"
          correlation: "{corr}"
""".format(
        wf=wf_name, wfn=wf_ns, ns=ns, upc=upc, rc=rc,
        deadline=deadline, direction=direction,
        upc_label=upc_label, rc_label=rc_label,
        lat=lat, jit=jit, loss=loss, corr=corr
    )
