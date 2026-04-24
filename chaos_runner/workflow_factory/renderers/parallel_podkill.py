# -*- coding: utf-8 -*-
import random
import re
from chaos_runner.workflow_factory.renderers import register
from chaos_runner.workflow_factory.renderers.value_resolver import resolve_duration

@register("parallel_podkill")
def render(case, resolved, config):
    wf = case.get("workflow") or {}
    wf_name = wf.get("name") or case.get("name") or "wf"
    wf_ns = wf.get("namespace") or config.WF_NAMESPACE
    ns = config.NS_TARGET

    kill = case.get("kill") or {}
    items = kill.get("items") or []
    if not items:
        raise RuntimeError("kill.items is empty")

    # plan: list of dicts {pod: name, delay: duration string}
    plan = []

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
        """
        val_list: [{"pod": "...", "ip":"..."}, ...]
        expand_cfg:
          - "all"
          - {"mode":"random","count":1}
          - {"indices":[0,2]}
        """
        if expand_cfg is None:
            raise RuntimeError("target {} is list; please set expand (all / random+count / indices)".format(tid))

        # expand: all
        if expand_cfg == "all":
            return val_list

        # expand: { ... }
        if isinstance(expand_cfg, dict):
            # indices
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

            # random + count
            mode = (expand_cfg.get("mode") or "").lower()
            if mode == "random":
                count = int(expand_cfg.get("count", 1))
                if count < 1:
                    raise RuntimeError("expand.count must be >=1 for target {}".format(tid))
                if count > len(val_list):
                    raise RuntimeError("expand.count={} > available {} for target {}".format(count, len(val_list), tid))
                # 可选：支持 seed，便于复现
                seed = expand_cfg.get("seed", None)
                if seed is not None:
                    random.seed(seed)
                return random.sample(val_list, count)

        raise RuntimeError("invalid expand for target {}: {}".format(tid, expand_cfg))

    for it in items:
        tid = it["target"]
        raw_delay = it.get("delay", 0)
        delay = resolve_duration(raw_delay, field_name="kill.items.delay")
        expand_cfg = it.get("expand")

        val = resolved.get(tid)
        if val is None:
            raise RuntimeError("unknown target id: {}".format(tid))

        # 单目标：dict
        if isinstance(val, dict):
            plan.append({"pod": val["pod"], "delay": delay})
            continue

        # 列表目标：list
        if isinstance(val, list):
            selected = _expand_list_target(tid, val, expand_cfg)
            for x in selected:
                plan.append({"pod": x["pod"], "delay": delay})
            continue

        raise RuntimeError("unsupported target type: {} for {}".format(type(val), tid))

    # ===== 生成 workflow =====
    branches = []
    templates = []

    for i, it in enumerate(plan):
        pod = it["pod"]
        delay = it["delay"]
        b = "branch-{}".format(i)
        branches.append(b)

        if not _is_zero_delay(delay):
            templates.append("""
    - name: wait-{i}
      templateType: Suspend
      deadline: {d}
""".format(i=i, d=delay))
            children = "\n        - ".join(["wait-{}".format(i), "kill-{}".format(i)])
        else:
            children = "kill-{}".format(i)

        templates.append("""
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
        - {branches}

{templates}
""".format(
        wf=wf_name,
        wfn=wf_ns,
        branches="\n        - ".join(branches),
        templates="".join(templates).rstrip("\n")
    )
