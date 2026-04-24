# -*- coding: utf-8 -*-
from chaos_runner.tools.k8s import get_service_cluster_ip, get_pod_ip
import time
from chaos_runner.tools.http import http_get_json
from chaos_runner import config


def fetch_rc_cluster():
    """
    GET /api/paas/v1/maintenance/rc/cluster
    返回包含 rc_cluster_info + etcd_cluster_info

    Retries are applied for transient HTTP timeout/network errors to reduce
    flakiness when RC API is briefly slow.
    """
    cip = get_service_cluster_ip(config.NS_TARGET, config.RC_SVC_NAME)
    url = "http://{}:{}{}".format(cip, config.RC_API_PORT, config.RC_CLUSTER_API_PATH)

    timeout = int(getattr(config, "RC_HTTP_TIMEOUT", 5) or 5)
    retries = int(getattr(config, "RC_HTTP_RETRIES", 0) or 0)
    backoff = float(getattr(config, "RC_HTTP_RETRY_BACKOFF_SECONDS", 0.5) or 0.5)

    last_error = None
    for attempt in range(retries + 1):
        try:
            data = http_get_json(url, timeout=timeout)
            return data, url
        except RuntimeError as e:
            last_error = e
            if attempt >= retries:
                break
            time.sleep(backoff * (attempt + 1))

    raise RuntimeError("fetch_rc_cluster failed after {} attempt(s): {}".format(retries + 1, last_error))


def find_rc_leader(cluster_state):
    rc_info = ((cluster_state.get("rc_cluster_info") or {}).get("rc_info")) or []
    for x in rc_info:
        if (x.get("role", "") or "").lower() == "leader":
            pod = x.get("pod_name", "")
            addr = x.get("addr", "")
            ip = addr.split(":")[0] if addr else ""
            if not pod or not ip:
                raise RuntimeError("RC leader found but missing fields: {}".format(x))
            return {"pod": pod, "ip": ip}
    raise RuntimeError("No RC leader found in rc_cluster_info.rc_info")


def find_etcd_leader(cluster_state):
    """
    etcd_cluster_info.Endpoints[].Leader 为 0/1（不是 true/false）
    Endpoint 形如: dupf-etcd-1.dupf-etcd-headless:2379
    pod_name = dupf-etcd-1
    """
    etcd = cluster_state.get("etcd_cluster_info") or {}
    eps = etcd.get("Endpoints") or []

    leader_ep = None
    for e in eps:
        if int(e.get("Leader", 0)) == 1:
            leader_ep = e.get("Endpoint", "")
            break

    if not leader_ep:
        raise RuntimeError("No etcd leader found in etcd_cluster_info.Endpoints")

    host = leader_ep.split(":")[0].strip().rstrip(".")   # dupf-etcd-1.dupf-etcd-headless
    pod = host.split(".")[0]                              # dupf-etcd-1

    # pod IP 直接从 k8s 拿，避免解析 10-233-xx-xx 这种形式
    ip = get_pod_ip(config.NS_TARGET, pod)
    return {"pod": pod, "ip": ip, "endpoint": leader_ep}

# Added functions to find followers and full pod lists
def find_rc_followers(cluster_state):
    """
    Return a list of Registry Center (RC) pods that are not leaders.

    The cluster state comes from :func:`fetch_rc_cluster` and contains
    rc_cluster_info.rc_info where each entry has 'role', 'pod_name' and 'addr'.
    """
    rc_info = ((cluster_state.get("rc_cluster_info") or {}).get("rc_info")) or []
    out = []
    for x in rc_info:
        role = (x.get("role", "") or "").lower()
        if role == "leader":
            continue
        pod = x.get("pod_name", "")
        addr = x.get("addr", "")
        ip = addr.split(":")[0] if addr else ""
        if pod and ip:
            out.append({"pod": pod, "ip": ip})
    return out


def find_rc_pods(cluster_state):
    """
    Return a list of all RC pods regardless of role.

    Each element contains 'pod' and 'ip'.
    """
    rc_info = ((cluster_state.get("rc_cluster_info") or {}).get("rc_info")) or []
    out = []
    for x in rc_info:
        pod = x.get("pod_name", "")
        addr = x.get("addr", "")
        ip = addr.split(":")[0] if addr else ""
        if pod and ip:
            out.append({"pod": pod, "ip": ip})
    return out


def find_etcd_followers(cluster_state):
    """
    Return a list of etcd pods that are not leaders.

    Leverages the cluster state from :func:`fetch_rc_cluster`, which includes
    etcd_cluster_info.Endpoints with Leader flag (0/1).
    """
    etcd = cluster_state.get("etcd_cluster_info") or {}
    eps = etcd.get("Endpoints") or []
    out = []
    for e in eps:
        # skip leader
        try:
            leader_flag = int(e.get("Leader", 0))
        except Exception:
            leader_flag = 0
        if leader_flag == 1:
            continue
        endpoint = e.get("Endpoint", "")
        if not endpoint:
            continue
        host = endpoint.split(":")[0].strip().rstrip(".")
        # host might be like "dupf-etcd-1.dupf-etcd-headless"; extract pod prefix
        pod = host.split(".")[0] if host else ""
        if not pod:
            continue
        # look up pod IP via kubernetes
        ip = get_pod_ip(config.NS_TARGET, pod)
        out.append({"pod": pod, "ip": ip, "endpoint": endpoint})
    return out


def find_etcd_pods(cluster_state):
    """
    Return a list of all etcd pods from the cluster state (including leader).

    Each element includes 'pod', 'ip' and 'endpoint'.
    """
    etcd = cluster_state.get("etcd_cluster_info") or {}
    eps = etcd.get("Endpoints") or []
    out = []
    for e in eps:
        endpoint = e.get("Endpoint", "")
        if not endpoint:
            continue
        host = endpoint.split(":")[0].strip().rstrip(".")
        pod = host.split(".")[0] if host else ""
        if not pod:
            continue
        ip = get_pod_ip(config.NS_TARGET, pod)
        out.append({"pod": pod, "ip": ip, "endpoint": endpoint, "leader": int(e.get("Leader", 0))})
    return out

