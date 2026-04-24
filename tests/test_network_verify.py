# -*- coding: utf-8 -*-
from chaos_runner.executor import network_verify


def test_extract_netem_metrics_reads_delay_jitter_and_loss():
    text = "qdisc netem 1: root refcnt 2 limit 1000 delay 300.0ms 1000.0ms loss 10.0%"

    result = network_verify._extract_netem_metrics(text)

    assert result["delay_ms"] == 300.0
    assert result["jitter_ms"] == 1000.0
    assert result["loss_pct"] == 10.0


def test_format_netem_metrics_outputs_concise_summary():
    result = network_verify._format_netem_metrics(
        {
            "delay_ms": 300.0,
            "jitter_ms": 1000.0,
            "loss_pct": 10.0,
        }
    )

    assert result == "delay=300.000ms jitter=1000.000ms loss=10.000%"


def test_collect_pod_runtime_states_samples_each_pod_once(monkeypatch):
    pid_calls = []
    tc_calls = []

    def fake_resolve(node, namespace, pod):
        pid_calls.append((node, namespace, pod))
        return (100 + len(pid_calls), "")

    def fake_qdisc(node, pid):
        tc_calls.append((node, pid))
        return (0, "qdisc netem 1: root refcnt 2 limit 1000 loss 10.0%", "")

    monkeypatch.setattr(network_verify, "_resolve_pod_pid_on_node", fake_resolve)
    monkeypatch.setattr(network_verify, "_get_qdisc_on_pid", fake_qdisc)
    monkeypatch.setattr(network_verify, "_is_local_node", lambda node: False)

    pod_meta = {
        "pod-a": {"node": "node-1"},
        "pod-b": {"node": "node-2"},
    }

    result = network_verify._collect_pod_runtime_states("ns-demo", ["pod-a", "pod-b"], pod_meta, workers=4)

    assert sorted(pid_calls) == [("node-1", "ns-demo", "pod-a"), ("node-2", "ns-demo", "pod-b")]
    assert len(tc_calls) == 2
    assert result["pod-a"]["metrics"]["loss_pct"] == 10.0
    assert result["pod-b"]["metrics"]["loss_pct"] == 10.0
