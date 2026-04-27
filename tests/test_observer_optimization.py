# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, timezone

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


def test_collect_pod_runtime_logs_can_be_disabled(monkeypatch):
    monkeypatch.setattr(observer.config, "OBSERVER_RUNTIME_LOGS_ENABLED", False)
    monkeypatch.setattr(
        observer,
        "_collect_single_pod_runtime_log",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not collect logs")),
    )

    result = observer._collect_pod_runtime_logs("ns-demo", ["pod-a"])

    assert result == []


def test_parse_kubectl_top_pod_output():
    text = "\n".join(
        [
            "NAME                 CPU(cores)   MEMORY(bytes)",
            "pod-b                23m          41Mi",
            "pod-a                122m         49Mi",
        ]
    )

    result = observer._parse_kubectl_top_pod(text)

    assert result == [
        {"pod": "pod-a", "cpu": "122m", "memory": "49Mi"},
        {"pod": "pod-b", "cpu": "23m", "memory": "41Mi"},
    ]


def test_collect_pod_resource_usage_uses_kubectl_top(monkeypatch):
    calls = []
    monkeypatch.setattr(observer.config, "OBSERVER_RESOURCE_USAGE_ENABLED", True)

    def fake_sh(cmd, check=True):
        calls.append((cmd, check))
        return "\n".join(
            [
                "NAME CPU(cores) MEMORY(bytes)",
                "pod-a 10m 20Mi",
            ]
        )

    monkeypatch.setattr(observer, "sh", fake_sh)

    result = observer._collect_pod_resource_usage("ns-demo")

    assert calls == [("kubectl -n ns-demo top pod --containers=false 2>&1", False)]
    assert result == {
        "enabled": True,
        "rows": [{"pod": "pod-a", "cpu": "10m", "memory": "20Mi"}],
        "error": "",
    }


def test_collect_pod_resource_usage_can_be_disabled(monkeypatch):
    monkeypatch.setattr(observer.config, "OBSERVER_RESOURCE_USAGE_ENABLED", False)
    monkeypatch.setattr(
        observer,
        "sh",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not call kubectl top")),
    )

    result = observer._collect_pod_resource_usage("ns-demo")

    assert result == {"enabled": False, "rows": [], "error": "disabled"}


def test_collect_pod_resource_usage_records_unavailable(monkeypatch):
    monkeypatch.setattr(observer.config, "OBSERVER_RESOURCE_USAGE_ENABLED", True)
    monkeypatch.setattr(observer, "sh", lambda cmd, check=True: "error: Metrics API not available")

    result = observer._collect_pod_resource_usage("ns-demo")

    assert result == {
        "enabled": True,
        "rows": [],
        "error": "error: Metrics API not available",
    }


def test_dupf_host_log_dirs_expand_upu_related_series():
    dirs = observer._dupf_host_log_dirs_for_pod("dupf-upu-main-0")

    assert "/var/ctin/ctc-upf/var/log/service-logs/upu" in dirs
    assert "/var/ctin/ctc-upf/var/log/service-logs/upc" in dirs
    assert "/var/ctin/ctc-upf/var/log/service-logs/registry-center" in dirs
    assert "/var/ctin/ctc-upf/var/log/service-logs/mq-proxy" in dirs


def test_dupf_host_log_dirs_include_database_roots():
    dirs = observer._dupf_host_log_dirs_for_pod("dupf-ddb-shd-0-0")

    assert "/var/ctin/ctc-upf/ddb" in dirs
    assert "/var/ctin/ctc-upf/db-operator" in dirs
    assert "/var/ctin/ctc-upf/crash" in dirs


def test_dupf_host_log_dirs_include_db_operator_roots():
    dirs = observer._dupf_host_log_dirs_for_pod("dupf-db-operator-7c4b689ff9-cjjml")

    assert "/var/ctin/ctc-upf/db-operator" in dirs
    assert "/var/ctin/ctc-upf/ddb" in dirs
    assert "/var/ctin/ctc-upf/sdb" in dirs
    assert "/var/ctin/ctc-upf/sdb-sentinel" in dirs
    assert "/var/ctin/ctc-upf/crash" in dirs


def test_build_ddb_host_log_filter_command_uses_local_and_utc_windows(monkeypatch):
    monkeypatch.setattr(observer.config, "OBSERVER_DDB_LOG_TIMEZONE_OFFSET_HOURS", 8)
    monkeypatch.setattr(observer.config, "OBSERVER_DDB_LOG_LOOKBACK_HOURS", 3)
    monkeypatch.setattr(observer.config, "OBSERVER_DDB_LOG_FILE_COUNT", 50)

    cmd = observer._build_ddb_host_log_filter_command(
        since_time=datetime(2026, 4, 27, 1, 23, 14, tzinfo=timezone.utc),
        now=datetime(2026, 4, 27, 4, 23, 15, tzinfo=timezone.utc),
    )

    assert "local_start=20260427092314" in cmd
    assert "local_end=20260427122315" in cmd
    assert "utc_start=20260427012314" in cmd
    assert "utc_end=20260427042315" in cmd
    assert "find /var/ctin/ctc-upf/ddb -type f" in cmd
    assert "head -n 50" in cmd
    assert "WARN|WARNING|ERROR|EXCEPTION|FATAL|PANIC" in cmd
    assert '("x" t) >= ("x" start)' in cmd


def test_collect_dupf_ddb_host_logs_runs_on_configured_node_and_warn_filters(monkeypatch):
    monkeypatch.setattr(observer.config, "OBSERVER_DDB_HOST_LOGS_ENABLED", True)
    monkeypatch.setattr(observer.config, "OBSERVER_DDB_LOG_NODE", "solarserver02")
    monkeypatch.setattr(observer.config, "OBSERVER_DDB_LOG_MAX_LINES", 10)
    monkeypatch.setattr(observer.config, "OBSERVER_DDB_LOG_LOOKBACK_HOURS", 3)
    monkeypatch.setattr(observer.config, "OBSERVER_DDB_LOG_TIMEZONE_OFFSET_HOURS", 8)
    calls = []

    def fake_run_on_node(node, cmd):
        calls.append((node, cmd))
        return (
            0,
            "\n".join(
                [
                    "/var/ctin/ctc-upf/ddb/a.log 2026-04-27T01:23:15Z INFO ignored",
                    "/var/ctin/ctc-upf/ddb/a.log 2026-04-27T01:23:16Z WARN kept",
                    "/var/ctin/ctc-upf/ddb/a.log 2026/04/27 09:23:17 ERROR kept",
                ]
            ),
            "",
        )

    monkeypatch.setattr(observer, "_run_on_node", fake_run_on_node)

    result = observer._collect_dupf_ddb_host_logs(
        "ns-dupf",
        since_time=datetime(2026, 4, 27, 1, 23, 14, tzinfo=timezone.utc),
    )

    assert calls and calls[0][0] == "solarserver02"
    assert result[0]["component"] == "ddb"
    assert result[0]["source"].startswith("node_fs:/var/ctin/ctc-upf/ddb")
    assert result[0]["lines"] == [
        "/var/ctin/ctc-upf/ddb/a.log 2026-04-27T01:23:16Z WARN kept",
        "/var/ctin/ctc-upf/ddb/a.log 2026/04/27 09:23:17 ERROR kept",
    ]


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


def test_build_ssh_cmd_wraps_remote_command_in_shell(monkeypatch):
    monkeypatch.setattr(observer.config, "NET_VERIFY_REMOTE_SSH_USER", "gsta")
    monkeypatch.setattr(observer.config, "NET_VERIFY_SSH_PORT", 50163)
    monkeypatch.setattr(observer.config, "NET_VERIFY_SSH_EXTRA_OPTS", "-T")
    monkeypatch.setattr(observer.config, "NET_VERIFY_SSH_CONFIG_FILE", "/dev/null")

    cmd = observer._build_ssh_cmd("10.230.246.196", "test -d /tmp; echo ok | cat")

    assert "ssh -F /dev/null -T -p 50163 gsta@10.230.246.196" in cmd
    assert " sh -lc " in cmd
    assert "test -d /tmp; echo ok | cat" in cmd


def test_build_ssh_argv_preserves_remote_shell_operators(monkeypatch):
    monkeypatch.setattr(observer.config, "NET_VERIFY_REMOTE_SSH_USER", "gsta")
    monkeypatch.setattr(observer.config, "NET_VERIFY_SSH_PORT", 50163)
    monkeypatch.setattr(observer.config, "NET_VERIFY_SSH_EXTRA_OPTS", "-T")
    monkeypatch.setattr(observer.config, "NET_VERIFY_SSH_CONFIG_FILE", "/dev/null")

    argv = observer._build_ssh_argv("10.230.246.196", "test -d /tmp; echo ok | cat")

    assert argv[:3] == ["ssh", "-F", "/dev/null"]
    assert argv[-3:] == ["sh", "-lc", "'test -d /tmp; echo ok | cat'"]


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


def test_collect_single_pod_runtime_log_uses_dupf_node_logs_with_since_time(monkeypatch):
    monkeypatch.setattr(observer, "_uses_internal_smf_logs", lambda namespace, pod: False)
    monkeypatch.setattr(observer, "_uses_dupf_internal_logs", lambda namespace, pod: True)
    monkeypatch.setattr(observer, "sh", lambda cmd, check=True: "")

    internal_calls = []

    def fake_internal(namespace, pod, node=""):
        internal_calls.append((namespace, pod, node))
        return "\n".join(
            [
                "2026-04-23 10:20:29.999 ERROR old log",
                "2026-04-23 10:20:30.000 INFO new host info",
                "2026-04-23 10:20:31.000 DEBUG new host debug",
                "2026-04-23 10:20:32.000 WARN new host warning",
            ]
        )

    monkeypatch.setattr(observer, "_collect_internal_dupf_logs", fake_internal)

    result = observer._collect_single_pod_runtime_log(
        "ns-dupf",
        "dupf-upc-0",
        since_time=datetime(2026, 4, 23, 10, 20, 30, tzinfo=timezone.utc),
        pod_status_map={"dupf-upc-0": {"node": "node-1"}},
    )

    assert result["source"].startswith("node_fs:")
    assert result["lines"] == [
        "2026-04-23 10:20:30.000 INFO new host info",
        "2026-04-23 10:20:31.000 DEBUG new host debug",
        "2026-04-23 10:20:32.000 WARN new host warning",
    ]
    assert internal_calls == [("ns-dupf", "dupf-upc-0", "node-1")]


def test_collect_single_pod_runtime_log_always_fetches_dupf_node_logs(monkeypatch):
    monkeypatch.setattr(observer, "_uses_internal_smf_logs", lambda namespace, pod: False)
    monkeypatch.setattr(observer, "sh", lambda cmd, check=True: "2026-04-23 10:20:31.000 ERROR kubectl error")

    internal_calls = []

    def fake_internal(namespace, pod, node=""):
        internal_calls.append((namespace, pod, node))
        return "\n".join(
            [
                "2026-04-23 10:20:31.000 INFO node fs log",
                "2026-04-23 10:20:32.000 DEBUG node fs detail",
            ]
        )

    monkeypatch.setattr(observer, "_collect_internal_dupf_logs", fake_internal)

    result = observer._collect_single_pod_runtime_log(
        "ns-dupf",
        "dupf-upu-solarserver01-2-5986bf4db8-kdm75",
        since_time=datetime(2026, 4, 23, 10, 20, 30, tzinfo=timezone.utc),
        pod_status_map={"dupf-upu-solarserver01-2-5986bf4db8-kdm75": {"node": "solarserver01"}},
    )

    assert result["source"].startswith("node_fs:/var/ctin/ctc-upf/")
    assert result["lines"] == [
        "2026-04-23 10:20:31.000 INFO node fs log",
        "2026-04-23 10:20:32.000 DEBUG node fs detail",
    ]
    assert internal_calls == [("ns-dupf", "dupf-upu-solarserver01-2-5986bf4db8-kdm75", "solarserver01")]


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


def test_filter_runtime_log_lines_keeps_error_and_warn_lines_in_time_window():
    text = "\n".join(
        [
            "2026-04-23 10:20:29.999 INFO before window",
            "2026-04-23 10:20:30.000 ERROR first error",
            "2026-04-23 10:20:31.000 info ignored",
            "2026-04-23 10:20:32.000 WARN warning line",
            "2026-04-23 10:20:33.000 FATAL second error",
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
        "2026-04-23 10:20:32.000 WARN warning line",
        "2026-04-23 10:20:33.000 FATAL second error",
    ]


def test_filter_runtime_log_lines_converts_configured_log_timezone(monkeypatch):
    monkeypatch.setattr(observer.config, "OBSERVER_LOG_TIMEZONE_OFFSET_HOURS", 8)
    text = "\n".join(
        [
            "2026-04-23 10:20:29.999 ERROR before local window",
            "2026-04-23 10:20:30.000 ERROR first local error",
        ]
    )

    result = observer._filter_runtime_log_lines(
        text,
        10,
        since_time=datetime(2026, 4, 23, 2, 20, 30, tzinfo=timezone.utc),
        error_only=True,
        enforce_line_timestamp=True,
    )

    assert result == ["2026-04-23 10:20:30.000 ERROR first local error"]


def test_runtime_log_window_shows_log_time_minus_local_offset(monkeypatch):
    monkeypatch.setattr(observer.config, "OBSERVER_LOG_TIMEZONE_OFFSET_HOURS", 0)

    window = observer._runtime_log_window(datetime(2026, 4, 27, 20, 4, 21, tzinfo=timezone(timedelta(hours=8))))

    assert window["case_start_local"] == "2026-04-27 20:04:21"
    assert window["since_utc"] == "2026-04-27 12:04:21"
    assert window["log_since"] == "2026-04-27 12:04:21"
    assert window["log_tz"] == "UTC+0"


def test_collect_single_pod_runtime_log_keeps_error_and_warn_lines(monkeypatch):
    monkeypatch.setattr(
        observer,
        "sh",
        lambda cmd, check=True: "\n".join(
            [
                "INFO startup ok",
                "ERROR error should remain",
                "WARN warning should remain",
                "WARNING warning word should remain",
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

    assert result["lines"] == [
        "ERROR error should remain",
        "WARN warning should remain",
        "WARNING warning word should remain",
    ]
