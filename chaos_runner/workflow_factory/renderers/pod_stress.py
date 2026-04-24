# -*- coding: utf-8 -*-
import random

from chaos_runner.workflow_factory.renderers import register
from chaos_runner.workflow_factory.renderers.value_resolver import resolve_duration


def _select_targets(resolved, target_id, expand_cfg):
    val = resolved.get(target_id)
    if val is None:
        raise RuntimeError("unknown target id: {}".format(target_id))

    if isinstance(val, dict):
        return [val]

    if not isinstance(val, list):
        raise RuntimeError("unsupported target type: {} for {}".format(type(val), target_id))

    if expand_cfg == "all":
        return val

    if isinstance(expand_cfg, dict):
        if "indices" in expand_cfg:
            idxs = expand_cfg.get("indices") or []
            if not idxs:
                raise RuntimeError("stress.expand.indices must be a non-empty list")
            out = []
            for i in idxs:
                if not isinstance(i, int) or i < 0 or i >= len(val):
                    raise RuntimeError("stress.expand.indices has invalid index {} (len={})".format(i, len(val)))
                out.append(val[i])
            return out

        if (expand_cfg.get("mode") or "").lower() == "random":
            count = int(expand_cfg.get("count", 1))
            if count < 1 or count > len(val):
                raise RuntimeError("stress.expand.count must be in [1, {}]".format(len(val)))
            seed = expand_cfg.get("seed")
            if seed is not None:
                random.seed(seed)
            return random.sample(val, count)

    raise RuntimeError("target {} is a list; set stress.expand to all/random/indices".format(target_id))


def _resolve_stress_targets(stress_cfg):
    targets = stress_cfg.get("targets")
    if targets:
        if not isinstance(targets, list):
            raise RuntimeError("stress.targets must be a list")
        out = []
        for it in targets:
            if not isinstance(it, dict):
                raise RuntimeError("each item in stress.targets must be an object")
            tid = it.get("target")
            if not tid:
                raise RuntimeError("each stress.targets item must include target")
            out.append(it)
        return out

    # backward compatibility (single target)
    target_id = stress_cfg.get("target")
    if target_id:
        one = {
            "target": target_id,
            "expand": stress_cfg.get("expand"),
        }
        if "cpu" in stress_cfg:
            one["cpu"] = stress_cfg.get("cpu")
        if "memory" in stress_cfg:
            one["memory"] = stress_cfg.get("memory")
        return [one]

    raise RuntimeError("stress.target or stress.targets is required")


def _dedup_pods(pods):
    seen = set()
    out = []
    for p in pods:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _cpu_settings(stress_cfg, target_cfg):
    cfg = (stress_cfg.get("cpu") or {}).copy()
    cfg.update(target_cfg.get("cpu") or {})
    workers = int(cfg.get("workers", 1))
    load = int(cfg.get("load", 80))
    if workers < 1:
        raise RuntimeError("stress.cpu.workers must be >=1")
    if load < 1 or load > 100:
        raise RuntimeError("stress.cpu.load must be in 1..100")
    return workers, load


def _memory_settings(stress_cfg, target_cfg):
    cfg = (stress_cfg.get("memory") or {}).copy()
    cfg.update(target_cfg.get("memory") or {})
    workers = int(cfg.get("workers", 1))
    size = str(cfg.get("size", "256MB"))
    if workers < 1:
        raise RuntimeError("stress.memory.workers must be >=1")
    return workers, size


def _render_branch(i, wf_ns, ns, target_cfg, pod_names, duration, mode, stress_cfg):
    branch_name = "stress-{}".format(i)
    pods_yaml = "\n".join(["              - {}".format(p) for p in pod_names])

    if mode == "cpu":
        workers, load = _cpu_settings(stress_cfg, target_cfg)
        stressor_yaml = """          cpu:
            workers: {workers}
            load: {load}
""".format(workers=workers, load=load)
    else:
        workers, size = _memory_settings(stress_cfg, target_cfg)
        stressor_yaml = """          memory:
            workers: {workers}
            size: \"{size}\"
""".format(workers=workers, size=size)

    return branch_name, """
    - name: {name}
      templateType: StressChaos
      deadline: {duration}
      stressChaos:
        mode: all
        selector:
          namespaces:
            - {ns}
          pods:
            {ns}:
{pods}
        stressors:
{stressor}
""".format(name=branch_name, duration=duration, ns=ns, pods=pods_yaml, stressor=stressor_yaml.rstrip("\n"))


def _render(case, resolved, config, mode):
    wf = case.get("workflow") or {}
    wf_name = wf.get("name") or case.get("name") or "wf"
    wf_ns = wf.get("namespace") or config.WF_NAMESPACE
    ns = config.NS_TARGET

    stress = case.get("stress") or {}
    target_defs = _resolve_stress_targets(stress)

    branches = []
    templates = []
    for i, item in enumerate(target_defs):
        duration = resolve_duration(item.get("duration", stress.get("duration", "30s")), field_name="stress.duration", default="30s")
        selected = _select_targets(resolved, item["target"], item.get("expand"))
        pod_names = [x.get("pod") for x in selected if isinstance(x, dict) and x.get("pod")]
        pod_names = _dedup_pods(pod_names)
        if not pod_names:
            raise RuntimeError("stress target {} resolves to empty pod list".format(item.get("target")))

        name, tpl = _render_branch(i, wf_ns, ns, item, pod_names, duration, mode, stress)
        branches.append(name)
        templates.append(tpl)

    return """apiVersion: chaos-mesh.org/v1alpha1
kind: Workflow
metadata:
  name: \"{wf}\"
  namespace: \"{wfn}\"
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
        templates="".join(templates).rstrip("\n"),
    )


@register("cpu_stress_parallel")
@register("cpu_stress_single_role")
def render_cpu_stress(case, resolved, config):
    return _render(case, resolved, config, mode="cpu")


@register("memory_stress_parallel")
@register("memory_stress_single_role")
def render_memory_stress(case, resolved, config):
    return _render(case, resolved, config, mode="memory")
