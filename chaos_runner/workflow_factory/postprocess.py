# -*- coding: utf-8 -*-
import json

from chaos_runner import yaml_compat as yaml

from chaos_runner.tools.k8s import sh


def _component_of_pod(name):
    low = (name or "").lower()
    if "ddb" in low:
        return "ddb"
    if "etcd" in low:
        return "etcd"
    if "registry" in low or "-rc-" in low or "dupf-rc" in low:
        return "rc"
    if "upc" in low or "upu" in low:
        return "upc"
    if "sdb" in low:
        return "sdb"
    return "other"


def _network_group_of_pod(name):
    """Return finer-grained group for NetworkChaos expansion."""
    low = (name or "").lower()
    if "upc-lb" in low:
        return "upc-lb"
    if "upu" in low:
        return "upu"
    if "etcd" in low:
        return "etcd"
    if "registry" in low or "-rc-" in low or "dupf-rc" in low:
        return "rc"
    if "ddb" in low:
        return "ddb"
    if "sdb" in low:
        return "sdb"
    if "upc" in low:
        return "upc"
    return _component_of_pod(name)


def _list_namespace_pods(namespace):
    data = json.loads(sh("kubectl -n {} get pod -o json".format(namespace)))
    names = []
    for it in data.get("items", []):
        name = ((it.get("metadata") or {}).get("name") or "").strip()
        if name:
            names.append(name)
    return names


def expand_network_chaos_to_component_pods(wf_yaml_text, namespace):
    """
    Expand NetworkChaos pods selectors to all pods in the same component(s).

    Example: if selector currently has one etcd pod, it becomes all etcd pods;
    if target has one upc-lb pod, it becomes all upc pods.
    """
    doc = yaml.safe_load(wf_yaml_text) or {}
    spec = doc.get("spec") or {}
    templates = spec.get("templates") or []

    all_pods = _list_namespace_pods(namespace)
    by_comp = {}
    by_group = {}
    for p in all_pods:
        comp = _component_of_pod(p)
        by_comp.setdefault(comp, []).append(p)
        grp = _network_group_of_pod(p)
        by_group.setdefault(grp, []).append(p)

    changed = False

    for tpl in templates:
        if (tpl or {}).get("templateType") != "NetworkChaos":
            continue
        net = (tpl or {}).get("networkChaos") or {}

        selector = (net.get("selector") or {}).get("pods") or {}
        src = selector.get(namespace)
        if isinstance(src, list) and src:
            groups = sorted({_network_group_of_pod(p) for p in src if _network_group_of_pod(p) != "other"})
            expanded = sorted({p for g in groups for p in by_group.get(g, [])})
            if not expanded:
                comps = sorted({_component_of_pod(p) for p in src if _component_of_pod(p) != "other"})
                expanded = sorted({p for c in comps for p in by_comp.get(c, [])})
            if expanded and expanded != src:
                selector[namespace] = expanded
                changed = True

        target = ((net.get("target") or {}).get("selector") or {}).get("pods") or {}
        dst = target.get(namespace)
        if isinstance(dst, list) and dst:
            groups = sorted({_network_group_of_pod(p) for p in dst if _network_group_of_pod(p) != "other"})
            expanded = sorted({p for g in groups for p in by_group.get(g, [])})
            if not expanded:
                comps = sorted({_component_of_pod(p) for p in dst if _component_of_pod(p) != "other"})
                expanded = sorted({p for c in comps for p in by_comp.get(c, [])})
            if expanded and expanded != dst:
                target[namespace] = expanded
                changed = True

    if not changed:
        return wf_yaml_text
    try:
        # PyYAML >= 5.1 supports sort_keys
        return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
    except TypeError:
        # PyYAML on older Python environments (e.g. py3.6 distro package)
        # does not accept sort_keys.
        return yaml.safe_dump(doc, allow_unicode=True)
