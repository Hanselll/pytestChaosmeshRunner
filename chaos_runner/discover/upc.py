# -*- coding: utf-8 -*-
import json
import re

from chaos_runner.tools.k8s import sh, get_pod_ip
from chaos_runner import config


def _list_upc_pod_names():
    """
    Return UPC data-plane pod names (exclude upc-lb).

    Preferred strategy is label exact match:
      app.kubernetes.io/component=dupf-pod-upc

    Fallback keeps backward compatibility with name-hint based matching, while
    explicitly excluding upc-lb style pods to avoid misclassification.
    """
    raw = sh(
        "kubectl -n {} get pod -l app.kubernetes.io/component=dupf-pod-upc "
        "-o jsonpath='{{.items[*].metadata.name}}'".format(config.NS_TARGET),
        check=False,
    )
    names = [n for n in (raw or "").strip().split() if n]
    if names:
        return names

    # Fallback for legacy environments where the exact component label is not
    # present on UPC pods.
    out_raw = sh("kubectl get pod -n {} -o jsonpath='{{.items[*].metadata.name}}'".format(config.NS_TARGET))
    all_names = [n for n in (out_raw.strip() or "").split() if n]
    out = []
    for pod in all_names:
        low = pod.lower()
        if config.UPC_PODNAME_HINT.lower() not in low:
            continue
        # Avoid treating upc-lb as UPC data-plane pod.
        if "upc-lb" in low:
            continue
        out.append(pod)
    return out

def _find_oam_pod():
    out = sh("kubectl get pod -n {} -o wide".format(config.NS_TARGET))
    for line in out.splitlines():
        if line.startswith("NAME"):
            continue
        if "oam" in line:
            return line.split()[0]
    raise RuntimeError("Cannot find oam pod in {}".format(config.NS_TARGET))


def _list_upc_pods_with_ip():
    out = []
    for pod in _list_upc_pod_names():
        ip = get_pod_ip(config.NS_TARGET, pod)
        if ip:
            out.append({"pod": pod, "ip": ip})
    return out


def _query_upc_role(ip):
    cmd = (
        "curl -sS --max-time 5 -X GET http://{}:3333/show/rsc_info".format(ip)
    )
    out = sh(cmd, check=False)
    if not out:
        raise RuntimeError("empty response")

    try:
        data = json.loads(out)
        role = str(data.get("upc_role", "")).strip()
        if role:
            return role
    except Exception:
        pass

    m = re.search(r'"upc_role"\s*:\s*"([^"]+)"', out)
    if m:
        return m.group(1).strip()

    raise RuntimeError("upc_role not found in response")


def _collect_upc_roles():
    rows = []
    for item in _list_upc_pods_with_ip():
        pod = item.get("pod")
        ip = item.get("ip")
        try:
            role = _query_upc_role(ip)
            rows.append({"pod": pod, "ip": ip, "role": role})
        except Exception as e:
            rows.append({"pod": pod, "ip": ip, "role": "", "error": str(e)})
    return rows


def find_upc_talker():
    rows = _collect_upc_roles()
    talkers = []
    details = []
    for row in rows:
        role = (row.get("role") or "").strip().lower()
        if role == "talker role":
            talkers.append(row)
        details.append(
            "{}({}) role={} error={}".format(
                row.get("pod", ""),
                row.get("ip", ""),
                row.get("role", "") or "<unknown>",
                row.get("error", "") or "<none>",
            )
        )
    if len(talkers) == 1:
        row = talkers[0]
        return {"pod": row.get("pod"), "ip": row.get("ip"), "role": row.get("role")}
    if not talkers:
        raise RuntimeError("No UPC talker found by curl role check. details={}".format("; ".join(details)))
    raise RuntimeError(
        "Multiple UPC talkers found by curl role check: {}. details={}".format(
            ", ".join(["{}({})".format(x.get("pod", ""), x.get("ip", "")) for x in talkers]),
            "; ".join(details),
        )
    )

# Added function to find UPC pods excluding the talker
def find_upc_non_talkers():
    """
    Return a list of UPC pods that are not acting as the talker.

    The talker IP and pod are determined using :func:`find_upc_talker`. We then
    list all pods in the target namespace and filter those whose name
    includes the configured UPC hint (e.g. ``upc``) and does not equal the
    talker pod. Each result includes ``pod`` and ``ip``.
    """
    rows = _collect_upc_roles()
    out = []
    for row in rows:
        role = (row.get("role") or "").strip().lower()
        if role == "talker role":
            continue
        if row.get("error"):
            continue
        out.append({"pod": row.get("pod"), "ip": row.get("ip"), "role": row.get("role")})
    return out

# Added function to list all UPC pods (including the talker)
def find_upc_pods():
    """
    Return a list of all UPC pods in the target namespace.

    Uses the configured UPC hint to filter pod names. Each entry contains
    ``pod`` and ``ip``.
    """
    names = _list_upc_pod_names()
    out = []
    for pod in names:
        ip = get_pod_ip(config.NS_TARGET, pod)
        out.append({"pod": pod, "ip": ip})
    return out
