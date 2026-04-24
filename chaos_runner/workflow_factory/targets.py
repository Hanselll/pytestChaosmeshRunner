# -*- coding: utf-8 -*-
from chaos_runner.discover.upc import find_upc_talker, find_upc_non_talkers, find_upc_pods
from chaos_runner.discover.smf import find_smf_pods
#from chaos_runner.discover.rc import fetch_cluster_state, find_rc_leader
#from chaos_runner.discover.rc import fetch_rc_health, find_rc_leader
import chaos_runner.discover.rc as rc_discover
from chaos_runner.discover.pods import find_pods_by_label, find_pods_by_label_values, find_pods_by_label_prefix, find_pods_by_name_prefix
from chaos_runner.discover.ddb import (
    find_ddb_masters,
    find_ddb_non_masters,
    find_ddb_shard_master,
    find_ddb_shard_slaves,
    find_ddb_other_shard_pods,
    find_ddb_shard_master_peers,
    find_ddb_pods,
)
from chaos_runner.discover.sdb import (
    find_sdb_master,
    find_sdb_pods,
    find_sdb_slaves,
    find_sdb_sentinel_info,
)


_DEFAULT_TARGET_ALIASES_BY_FINDER = {
    # Backward-compat: many cases reference target id "etcd" in selectors.
    # If a case defines rc_etcd_leader using another id, also expose "etcd".
    "rc_etcd_leader": "etcd",
}


def _call_optional_rc_finder(func_name, cluster_state):
    finder = getattr(rc_discover, func_name, None)
    if finder is None:
        raise RuntimeError("finder {} is not supported by this chaos_runner build".format(func_name))
    return finder(cluster_state)

def resolve_targets(case):
    ctx={"rc_cluster_state": None}
    resolved={}
    for t in case.get("targets", []):
        tid=t["id"]; finder=t["finder"]
        if finder=="upc_talker":
            resolved[tid]=find_upc_talker()
        elif finder == "upc_non_talkers":
            # return list of UPC pods excluding the talker
            resolved[tid] = find_upc_non_talkers()
        elif finder == "upc_pods":
            # return all UPC pods
            resolved[tid] = find_upc_pods()
        elif finder == "smf_pods":
            resolved[tid] = find_smf_pods()
        elif finder == "rc_leader":
            if ctx.get("rc_cluster_state") is None:
                data, url = rc_discover.fetch_rc_cluster()
                ctx["rc_cluster_state"] = data
                ctx["rc_cluster_url"] = url
            resolved[tid] = rc_discover.find_rc_leader(ctx["rc_cluster_state"])
        elif finder == "rc_followers":
            if ctx.get("rc_cluster_state") is None:
                data, url = rc_discover.fetch_rc_cluster()
                ctx["rc_cluster_state"] = data
                ctx["rc_cluster_url"] = url
            resolved[tid] = _call_optional_rc_finder("find_rc_followers", ctx["rc_cluster_state"])
        elif finder == "rc_pods":
            if ctx.get("rc_cluster_state") is None:
                data, url = rc_discover.fetch_rc_cluster()
                ctx["rc_cluster_state"] = data
                ctx["rc_cluster_url"] = url
            resolved[tid] = _call_optional_rc_finder("find_rc_pods", ctx["rc_cluster_state"])
        
        elif finder == "rc_etcd_leader":
            if ctx.get("rc_cluster_state") is None:
                data, url = rc_discover.fetch_rc_cluster()
                ctx["rc_cluster_state"] = data
                ctx["rc_cluster_url"] = url
            resolved[tid] = rc_discover.find_etcd_leader(ctx["rc_cluster_state"])
        elif finder == "etcd_followers":
            if ctx.get("rc_cluster_state") is None:
                data, url = rc_discover.fetch_rc_cluster()
                ctx["rc_cluster_state"] = data
                ctx["rc_cluster_url"] = url
            resolved[tid] = _call_optional_rc_finder("find_etcd_followers", ctx["rc_cluster_state"])
        elif finder == "etcd_pods":
            if ctx.get("rc_cluster_state") is None:
                data, url = rc_discover.fetch_rc_cluster()
                ctx["rc_cluster_state"] = data
                ctx["rc_cluster_url"] = url
            resolved[tid] = _call_optional_rc_finder("find_etcd_pods", ctx["rc_cluster_state"])

        elif finder=="ddb_masters":
            resolved[tid]=find_ddb_masters()
        elif finder=="ddb_non_masters":
            resolved[tid] = find_ddb_non_masters()
        elif finder == "ddb_pods":
            role = t.get("role", "all")
            shard_scope = t.get("shard_scope", "all")
            shard = t.get("shard")
            if str(shard_scope).strip().lower() in ("in", "not_in") and (shard is None or str(shard).strip() == ""):
                raise RuntimeError("finder=ddb_pods requires 'shard' when shard_scope is in/not_in in target {}".format(tid))
            resolved[tid] = find_ddb_pods(role=role, shard=shard, shard_scope=shard_scope)
        elif finder == "ddb_shard_master":
            shard = t.get("shard")
            if shard is None or str(shard).strip() == "":
                raise RuntimeError("finder=ddb_shard_master requires 'shard' field in target {}".format(tid))
            resolved[tid] = find_ddb_shard_master(shard)
        elif finder == "ddb_shard_slaves":
            shard = t.get("shard")
            if shard is None or str(shard).strip() == "":
                raise RuntimeError("finder=ddb_shard_slaves requires 'shard' field in target {}".format(tid))
            resolved[tid] = find_ddb_shard_slaves(shard)
        elif finder == "ddb_other_shard_pods":
            shard = t.get("shard")
            if shard is None or str(shard).strip() == "":
                raise RuntimeError("finder=ddb_other_shard_pods requires 'shard' field in target {}".format(tid))
            resolved[tid] = find_ddb_other_shard_pods(shard)
        elif finder == "ddb_shard_master_peers":
            shard = t.get("shard")
            if shard is None or str(shard).strip() == "":
                raise RuntimeError("finder=ddb_shard_master_peers requires 'shard' field in target {}".format(tid))
            resolved[tid] = find_ddb_shard_master_peers(shard)
        elif finder == "sdb_master":
            # Resolve the master pod of the SDB Redis cluster.  Returns a
            # single dict with ``pod`` and ``ip`` keys.
            resolved[tid] = find_sdb_master()
        elif finder == "sdb_slaves":
            # Resolve all slave pods of the SDB Redis cluster.  Returns a list
            # of dicts with ``pod`` and ``ip`` keys for each slave.
            resolved[tid] = find_sdb_slaves()
        elif finder == "sdb_pods":
            resolved[tid] = find_sdb_pods()
        elif finder == "sdb_sentinel_info":
            # Retrieve sentinel monitoring information for the SDB cluster.
            # The returned dict includes fields such as sentinel_masters and
            # master_address as documented in discover.sdb._parse_sentinel_info.
            resolved[tid] = find_sdb_sentinel_info()
        elif finder == "by_label":
            # 从 target 配置中读取 label 字符串，例如 "app.kubernetes.io/component: dupf-pod-upu-3"
            label_kv = t.get("label")
            if not label_kv:
                raise RuntimeError(f"finder=by_label requires 'label' field in target {tid}")
            resolved[tid] = find_pods_by_label(label_kv)
        elif finder == "by_label_values":
            label_key = t.get("label_key")
            label_values = t.get("label_values")
            if not label_key:
                raise RuntimeError(f"finder=by_label_values requires 'label_key' field in target {tid}")
            if not label_values:
                raise RuntimeError(f"finder=by_label_values requires 'label_values' field in target {tid}")
            resolved[tid] = find_pods_by_label_values(label_key, label_values)
        elif finder == "by_label_prefix":
            # 从 target 配置中读取 label 前缀，例如
            # "app.kubernetes.io/component: dupf-pod-upu-"，匹配所有 upu-*。
            label_prefix = t.get("label_prefix") or t.get("label")
            if not label_prefix:
                raise RuntimeError(f"finder=by_label_prefix requires 'label_prefix' (or 'label') field in target {tid}")
            resolved[tid] = find_pods_by_label_prefix(label_prefix)
        elif finder == "by_name_prefix":
            name_prefix = t.get("name_prefix") or t.get("prefix") or t.get("name")
            if not name_prefix:
                raise RuntimeError(f"finder=by_name_prefix requires 'name_prefix' (or 'prefix') field in target {tid}")
            resolved[tid] = find_pods_by_name_prefix(name_prefix)
        else:
            raise RuntimeError("Unknown finder: {}".format(finder))

        alias = _DEFAULT_TARGET_ALIASES_BY_FINDER.get(finder)
        if alias and alias not in resolved:
            resolved[alias] = resolved[tid]

    return resolved
