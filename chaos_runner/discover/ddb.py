# -*- coding: utf-8 -*-
import os
import re
import tempfile
from chaos_runner.tools.k8s import exec_in_pod, get_ns_pod_ip_map
from chaos_runner import config


_DDB_CLUSTER_NODES_DUMP_PATH = os.path.join(tempfile.gettempdir(), "ddb_cluster_nodes.txt")


def _cluster_nodes_raw():
    cmd = 'export REDISCLI_AUTH="{auth}"; redis-cli -p {port} cluster nodes'.format(auth=config.REDIS_AUTH, port=int(config.REDIS_PORT))
    raw = exec_in_pod(config.NS_TARGET, config.DDB_EXEC_POD, cmd)
    open(_DDB_CLUSTER_NODES_DUMP_PATH, "w", encoding="utf-8").write(raw)
    return raw


def _parse_master_ips(raw):
    ips = []
    for line in (raw or "").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        addr = parts[1]
        flags = parts[2]
        if "master" not in flags:
            continue
        ip = addr.split(":")[0]
        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip) and ip not in ips:
            ips.append(ip)
    return ips


def _normalize_shard_tag(shard):
    s = str(shard).strip()
    if not s:
        raise RuntimeError("ddb shard is empty")
    if s.startswith("shd-"):
        return s
    return "shd-{}".format(s)


def _extract_shard_tag(pod_name):
    m = re.search(r"(shd-\d+)", pod_name or "")
    return m.group(1) if m else ""


def _match_shard_pod(pod_name, shard_tag):
    p = (pod_name or "").lower()
    t = shard_tag.lower()
    return ("{}-".format(t) in p) or p.endswith(t)


def _discover_ddb_pods():
    """
    Build one DDB topology snapshot and annotate each pod with role/shard.

    Returns list[dict] where each item includes:
    - pod
    - ip
    - role: master|slave
    - shard: shd-N (if detectable from pod name, else "")
    """
    raw = _cluster_nodes_raw()
    master_ips = _parse_master_ips(raw)

    ip2pod = get_ns_pod_ip_map(config.NS_TARGET)
    pod2ip = {pod: ip for ip, pod in ip2pod.items()}

    ddb_prefix = getattr(config, "DDB_POD_PREFIX", "dupf-ddb").lower()
    ddb_pods = sorted(pod for pod in pod2ip if ddb_prefix in pod.lower())

    master_pods = []
    miss = []
    for ip in master_ips:
        pod = ip2pod.get(ip)
        if not pod:
            miss.append(ip)
            continue
        master_pods.append(pod)
    if miss:
        raise RuntimeError("cannot map master ips to pods: {} raw={}".format(miss, _DDB_CLUSTER_NODES_DUMP_PATH))

    master_set = set(master_pods)
    out = []
    for pod in ddb_pods:
        out.append({
            "pod": pod,
            "ip": pod2ip.get(pod, ""),
            "role": "master" if pod in master_set else "slave",
            "shard": _extract_shard_tag(pod),
        })
    return out


def find_ddb_pods(role="all", shard=None, shard_scope="all"):
    """
    Generic DDB finder based on one topology snapshot.

    role: all|master|slave
    shard_scope:
      - all: ignore shard
      - in: only pods in provided shard
      - not_in: only pods not in provided shard
    """
    role = (role or "all").strip().lower()
    shard_scope = (shard_scope or "all").strip().lower()
    if role not in ("all", "master", "slave"):
        raise RuntimeError("invalid ddb role: {} (expect all|master|slave)".format(role))
    if shard_scope not in ("all", "in", "not_in"):
        raise RuntimeError("invalid ddb shard_scope: {} (expect all|in|not_in)".format(shard_scope))

    shard_tag = _normalize_shard_tag(shard) if shard_scope != "all" else None
    pods = _discover_ddb_pods()

    def keep(x):
        if role != "all" and x.get("role") != role:
            return False
        if shard_scope == "in":
            return _match_shard_pod(x.get("pod", ""), shard_tag)
        if shard_scope == "not_in":
            return not _match_shard_pod(x.get("pod", ""), shard_tag)
        return True

    return [{"pod": x["pod"], "ip": x.get("ip", "")} for x in pods if keep(x)]


def find_ddb_masters():
    return find_ddb_pods(role="master")


def find_ddb_non_masters():
    return find_ddb_pods(role="slave")


def find_ddb_shard_master(shard):
    """Find master pod in a specific DDB shard (e.g. shard='0' or 'shd-0')."""
    hits = find_ddb_pods(role="master", shard=shard, shard_scope="in")
    if not hits:
        raise RuntimeError("No DDB master found for shard {}".format(_normalize_shard_tag(shard)))
    if len(hits) > 1:
        raise RuntimeError("Multiple DDB masters found for shard {}: {}".format(_normalize_shard_tag(shard), [x.get("pod") for x in hits]))
    return hits[0]


def find_ddb_shard_slaves(shard):
    """Find slave pods in a specific DDB shard (e.g. shard='0' or 'shd-0')."""
    hits = find_ddb_pods(role="slave", shard=shard, shard_scope="in")
    if not hits:
        raise RuntimeError("No DDB slaves found for shard {}".format(_normalize_shard_tag(shard)))
    return hits


def find_ddb_other_shard_pods(shard):
    """Find all DDB pods that do not belong to the specified shard."""
    hits = find_ddb_pods(role="all", shard=shard, shard_scope="not_in")
    if not hits:
        raise RuntimeError("No DDB pods found outside shard {}".format(_normalize_shard_tag(shard)))
    return hits


def find_ddb_shard_master_peers(shard):
    """
    Find peers to isolate from target shard master:
    - same shard slaves
    - all pods from other shards (masters + slaves)
    """
    shard_tag = _normalize_shard_tag(shard)
    pods = _discover_ddb_pods()
    hits = []
    for x in pods:
        in_target_shard = _match_shard_pod(x.get("pod", ""), shard_tag)
        if in_target_shard and x.get("role") == "master":
            continue
        hits.append({"pod": x.get("pod"), "ip": x.get("ip", "")})
    if not hits:
        raise RuntimeError("No DDB peers found for shard master isolation: {}".format(shard_tag))
    return hits
