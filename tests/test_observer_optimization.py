# -*- coding: utf-8 -*-
from datetime import datetime, timezone

from chaos_runner.executor import observer


def test_build_pod_status_map_filters_and_preserves_status():
    pod_items = [
        {
            "metadata": {"name": "pod-a"},
            "status": {"phase": "Running"},
            "spec": {"nodeName": "node-1"},
        },
        {
            "metadata": {"name": "pod-b"},
            "status": {"phase": "Pending"},
            "spec": {"nodeName": "node-2"},
        },
    ]

    result = observer._build_pod_status_map(pod_items, pod_names=["pod-b"])

    assert result == {"pod-b": {"status": "Pending", "node": "node-2"}}


def test_collect_pod_runtime_logs_reuses_pod_items_and_keeps_order(monkeypatch):
    pod_items = [
        {
            "metadata": {"name": "pod-a"},
            "status": {"phase": "Running"},
            "spec": {"nodeName": "node-1"},
        },
        {
            "metadata": {"name": "pod-b"},
            "status": {"phase": "Running"},
            "spec": {"nodeName": "node-2"},
        },
    ]
    calls = []

    def fake_collect(namespace, pod, since_time=None, pod_status_map=None):
        calls.append((namespace, pod, pod_status_map[pod]["node"]))
        return {
            "pod": pod,
            "node": pod_status_map[pod]["node"],
            "component": observer._component_of_pod(pod),
            "source": "fake",
            "lines": [pod],
            "empty": False,
        }

    monkeypatch.setattr(observer, "_collect_single_pod_runtime_log", fake_collect)
    monkeypatch.setattr(observer.config, "OBSERVER_LOG_MAX_WORKERS", 1)

    result = observer._collect_pod_runtime_logs(
        "ns-demo",
        ["pod-b", "pod-a"],
        pod_items=pod_items,
    )

    assert [item["pod"] for item in result] == ["pod-b", "pod-a"]
    assert calls == [("ns-demo", "pod-b", "node-2"), ("ns-demo", "pod-a", "node-1")]


def test_collect_target_events_rows_uses_supplied_pod_items(monkeypatch):
    calls = []

    def fake_sh(cmd, check=True):
        calls.append(cmd)
        return '{"items": []}'

    monkeypatch.setattr(observer, "sh", fake_sh)

    result = observer._collect_target_events_rows(
        "ns-demo",
        ["pod-a"],
        pod_items=[{"metadata": {"name": "pod-a", "ownerReferences": []}}],
    )

    assert result == []
    assert calls[0] == "kubectl get events -n ns-demo -o json"
    assert "kubectl -n ns-demo get pod -o json" not in calls


def test_collect_single_pod_runtime_log_uses_exact_since_time_without_previous(monkeypatch):
    calls = []
    responses = ["ERROR new log line", ""]

    def fake_sh(cmd, check=True):
        calls.append(cmd)
        return responses.pop(0)

    monkeypatch.setattr(observer, "sh", fake_sh)
    monkeypatch.setattr(observer, "_uses_internal_smf_logs", lambda namespace, pod: False)
    monkeypatch.setattr(observer, "_uses_dupf_internal_logs", lambda namespace, pod: False)

    since_time = datetime(2026, 4, 23, 10, 20, 30, tzinfo=timezone.utc)
    result = observer._collect_single_pod_runtime_log(
        "ns-demo",
        "pod-a",
        since_time=since_time,
        pod_status_map={"pod-a": {"node": "node-1"}},
    )

    assert result["source"] == "kubectl_logs"
    assert result["lines"] == ["ERROR new log line"]
    assert len(calls) == 1
    assert "--since-time=2026-04-23T10:20:30Z" in calls[0]
    assert "--previous" not in calls[0]


def test_collect_single_pod_runtime_log_skips_internal_fallback_when_since_time_set(monkeypatch):
    monkeypatch.setattr(observer, "_uses_internal_smf_logs", lambda namespace, pod: False)
    monkeypatch.setattr(observer, "_uses_dupf_internal_logs", lambda namespace, pod: True)
    monkeypatch.setattr(observer, "sh", lambda cmd, check=True: "")

    internal_calls = []

    def fake_internal(namespace, pod):
        internal_calls.append((namespace, pod))
        return "old log"

    monkeypatch.setattr(observer, "_collect_internal_dupf_logs", fake_internal)

    result = observer._collect_single_pod_runtime_log(
        "ns-demo",
        "pod-a",
        since_time=datetime(2026, 4, 23, 10, 20, 30, tzinfo=timezone.utc),
        pod_status_map={"pod-a": {"node": "node-1"}},
    )

    assert result["lines"] == []
    assert internal_calls == []


def test_collect_single_pod_runtime_log_uses_previous_with_same_since_time(monkeypatch):
    calls = []
    responses = ["", "ERROR line from previous container"]

    def fake_sh(cmd, check=True):
        calls.append(cmd)
        return responses.pop(0)

    monkeypatch.setattr(observer, "_uses_internal_smf_logs", lambda namespace, pod: False)
    monkeypatch.setattr(observer, "_uses_dupf_internal_logs", lambda namespace, pod: False)
    monkeypatch.setattr(observer, "sh", fake_sh)

    result = observer._collect_single_pod_runtime_log(
        "ns-demo",
        "pod-a",
        since_time=datetime(2026, 4, 23, 10, 20, 30, tzinfo=timezone.utc),
        pod_status_map={"pod-a": {"node": "node-1"}},
    )

    assert result["source"] == "kubectl_logs_previous"
    assert result["lines"] == ["ERROR line from previous container"]
    assert len(calls) == 2
    assert "--since-time=2026-04-23T10:20:30Z" in calls[0]
    assert "--since-time=2026-04-23T10:20:30Z" in calls[1]
    assert "--previous" in calls[1]


def test_filter_runtime_log_lines_keeps_only_error_lines_in_time_window():
    text = "\n".join(
        [
            "2026-04-23 10:20:29.999 INFO before window",
            "2026-04-23 10:20:30.000 ERROR first error",
            "2026-04-23 10:20:31.000 info ignored",
            "2026-04-23 10:20:32.000 FATAL second error",
            "line without timestamp should be skipped",
        ]
    )

    result = observer._filter_runtime_log_lines(
        text,
        10,
        since_time=datetime(2026, 4, 23, 10, 20, 30, tzinfo=timezone.utc),
        error_only=True,
        enforce_line_timestamp=True,
    )

    assert result == [
        "2026-04-23 10:20:30.000 ERROR first error",
        "2026-04-23 10:20:32.000 FATAL second error",
    ]


def test_collect_single_pod_runtime_log_keeps_only_error_lines(monkeypatch):
    monkeypatch.setattr(
        observer,
        "sh",
        lambda cmd, check=True: "\n".join(
            [
                "INFO startup ok",
                "ERROR only this should remain",
                "WARN ignored",
            ]
        ),
    )
    monkeypatch.setattr(observer, "_uses_internal_smf_logs", lambda namespace, pod: False)
    monkeypatch.setattr(observer, "_uses_dupf_internal_logs", lambda namespace, pod: False)

    result = observer._collect_single_pod_runtime_log(
        "ns-demo",
        "pod-a",
        since_time=datetime(2026, 4, 23, 10, 20, 30, tzinfo=timezone.utc),
        pod_status_map={"pod-a": {"node": "node-1"}},
    )

    assert result["lines"] == ["ERROR only this should remain"]
