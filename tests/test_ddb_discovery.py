# -*- coding: utf-8 -*-
from chaos_runner import config
from chaos_runner.discover import ddb


def test_discover_ddb_pods_tolerates_fewer_masters_than_expected(monkeypatch):
    monkeypatch.setattr(config, "NS_TARGET", "ns-demo")
    monkeypatch.setattr(config, "EXPECTED_MASTER_COUNT", 3)
    monkeypatch.setattr(config, "DDB_POD_PREFIX", "dupf-ddb")

    raw = "\n".join(
        [
            "id1 10.0.0.1:17380@27380 master - 0 1 1 connected 0-5461",
            "id2 10.0.0.2:17380@27380 slave id1 0 1 1 connected",
            "id3 10.0.0.3:17380@27380 master - 0 1 2 connected 5462-10922",
        ]
    )
    monkeypatch.setattr(ddb, "_cluster_nodes_raw", lambda: raw)
    monkeypatch.setattr(
        ddb,
        "get_ns_pod_ip_map",
        lambda namespace: {
            "10.0.0.1": "dupf-ddb-shd-0-0",
            "10.0.0.2": "dupf-ddb-shd-0-1",
            "10.0.0.3": "dupf-ddb-shd-1-0",
        },
    )

    pods = ddb._discover_ddb_pods()
    roles = {item["pod"]: item["role"] for item in pods}

    assert roles["dupf-ddb-shd-0-0"] == "master"
    assert roles["dupf-ddb-shd-1-0"] == "master"
    assert roles["dupf-ddb-shd-0-1"] == "slave"


def test_find_ddb_shard_master_reports_missing_shard_without_global_master_count_error(monkeypatch):
    monkeypatch.setattr(config, "NS_TARGET", "ns-demo")
    monkeypatch.setattr(config, "EXPECTED_MASTER_COUNT", 3)
    monkeypatch.setattr(config, "DDB_POD_PREFIX", "dupf-ddb")

    raw = "\n".join(
        [
            "id1 10.0.0.1:17380@27380 master - 0 1 1 connected 0-5461",
            "id2 10.0.0.2:17380@27380 slave id1 0 1 1 connected",
            "id3 10.0.0.3:17380@27380 master - 0 1 2 connected 5462-10922",
        ]
    )
    monkeypatch.setattr(ddb, "_cluster_nodes_raw", lambda: raw)
    monkeypatch.setattr(
        ddb,
        "get_ns_pod_ip_map",
        lambda namespace: {
            "10.0.0.1": "dupf-ddb-shd-0-0",
            "10.0.0.2": "dupf-ddb-shd-0-1",
            "10.0.0.3": "dupf-ddb-shd-1-0",
        },
    )

    try:
        ddb.find_ddb_shard_master("2")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert str(exc) == "No DDB master found for shard shd-2"
