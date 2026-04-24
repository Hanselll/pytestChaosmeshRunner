# chaos_runner/discover/pods.py
import json

from chaos_runner.tools.k8s import sh, get_pod_ip
from chaos_runner import config


def _list_all_pod_names():
    raw = sh(
        "kubectl -n {} get pod -o jsonpath='{{.items[*].metadata.name}}'".format(
            config.NS_TARGET
        )
    )
    return [n for n in (raw or "").split() if n]


def find_pods_by_label(label_kv: str):
    """
    根据 `key: value` 格式的 label 查询符合条件的所有 Pod。
    例如 label_kv = "app.kubernetes.io/component: dupf-pod-upu-3"
    返回 [{"pod": pod_name, "ip": pod_ip}, ...] 列表。
    """
    if ":" not in label_kv:
        raise RuntimeError("label must be in 'key: value' format")
    key, value = [part.strip() for part in label_kv.split(":", 1)]
    # 查询匹配该 label 的 Pod 名称
    raw = sh(
        f"kubectl -n {config.NS_TARGET} get pod -l {key}={value} "
        "-o jsonpath='{.items[*].metadata.name}'"
    )
    names = [n for n in raw.strip().split() if n]
    pods = []
    for pod in names:
        ip = get_pod_ip(config.NS_TARGET, pod)
        pods.append({"pod": pod, "ip": ip})
    return pods


def find_pods_by_label_values(label_key: str, label_values):
    key = str(label_key or "").strip()
    if not key:
        raise RuntimeError("label key must be a non-empty string")

    values = []
    seen = set()
    for item in (label_values or []):
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        values.append(text)
    if not values:
        raise RuntimeError("label values must be a non-empty list")

    raw = sh(f"kubectl -n {config.NS_TARGET} get pod -l {key} -o json")
    data = json.loads(raw or "{}")

    pods = []
    wanted = set(values)
    for item in data.get("items", []):
        md = item.get("metadata") or {}
        pod = (md.get("name") or "").strip()
        labels = md.get("labels") or {}
        val = str(labels.get(key, "")).strip()
        if not pod or val not in wanted:
            continue
        ip = get_pod_ip(config.NS_TARGET, pod)
        pods.append({"pod": pod, "ip": ip})
    return pods


def find_pods_by_name_prefix(name_prefix: str):
    """
    Return pods in target namespace whose names start with the given prefix.

    Example:
      name_prefix = "smf-siglb-0-"
      -> [{"pod": "smf-siglb-0-xxxx", "ip": "10.x.x.x"}]
    """
    prefix = str(name_prefix or "").strip()
    if not prefix:
        raise RuntimeError("name prefix must be a non-empty string")

    pods = []
    for pod in _list_all_pod_names():
        if not pod.startswith(prefix):
            continue
        ip = get_pod_ip(config.NS_TARGET, pod)
        pods.append({"pod": pod, "ip": ip})
    return pods


def find_pods_by_label_prefix(label_kv_prefix: str):
    """
    根据 `key: prefix` 形式匹配 label 值前缀，返回符合条件的 Pod 列表。

    例如 `app.kubernetes.io/component: dupf-pod-upu-` 可以匹配
    `dupf-pod-upu-1`、`dupf-pod-upu-2` ...。
    返回 [{"pod": pod_name, "ip": pod_ip}, ...] 列表。
    """
    if ":" not in label_kv_prefix:
        raise RuntimeError("label prefix must be in 'key: prefix' format")
    key, prefix = [part.strip() for part in label_kv_prefix.split(":", 1)]

    # 先按 key 过滤，避免拉取 namespace 全量 Pod。
    # 这里使用 -o json 后在 Python 里解析，避免复杂 jsonpath 在不同 kubectl 版本上的兼容问题。
    raw = sh(f"kubectl -n {config.NS_TARGET} get pod -l {key} -o json")
    data = json.loads(raw or "{}")

    pods = []
    for item in data.get("items", []):
        md = item.get("metadata") or {}
        pod = (md.get("name") or "").strip()
        labels = md.get("labels") or {}
        val = str(labels.get(key, "")).strip()
        if not pod:
            continue
        if val.startswith(prefix):
            ip = get_pod_ip(config.NS_TARGET, pod)
            pods.append({"pod": pod, "ip": ip})
    return pods
