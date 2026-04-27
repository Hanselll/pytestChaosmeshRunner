# -*- coding: utf-8 -*-
import json
import subprocess
from datetime import datetime, timedelta, timezone

from chaos_runner import config
from chaos_runner.executor import ems_alarm


def test_run_ems_alarm_query_invokes_existing_ems_cli(tmp_path, monkeypatch):
    ems_dir = tmp_path / "ems_automation"
    ems_dir.mkdir()
    output_dir = tmp_path / "alarm_out"

    monkeypatch.setattr(config, "EMS_ALARM_ENABLED", True)
    monkeypatch.setattr(config, "EMS_ALARM_TARGET", "all")
    monkeypatch.setattr(config, "EMS_ALARM_OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(config, "EMS_ALARM_TIMEOUT_SECONDS", 33)
    monkeypatch.setattr(config, "EMS_ALARM_TIMEZONE_OFFSET_HOURS", 0)
    monkeypatch.setattr(config, "EMS_ALARM_PYTHON", "python-test")
    monkeypatch.setattr(config, "EMS_AUTOMATION_DIR", str(ems_dir))
    monkeypatch.setattr(ems_alarm, "_local_timezone", lambda: timezone(timedelta(hours=8)))

    calls = []

    def fake_run(cmd, cwd=None, env=None, stdout=None, stderr=None, text=None, encoding=None, errors=None, timeout=None):
        calls.append(
            {
                "cmd": cmd,
                "cwd": cwd,
                "env": env,
                "stdout": stdout,
                "stderr": stderr,
                "text": text,
                "encoding": encoding,
                "errors": errors,
                "timeout": timeout,
            }
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "alarm_export_summary.json").write_text(
            json.dumps(
                {
                    "activity": {"rows": [{"id": "a1"}]},
                    "history": {"rows": [{"id": "h1"}, {"id": "h2"}]},
                }
            ),
            encoding="utf-8",
        )
        table = {
            "headers": [
                "confirm",
                "severity",
                "vendor",
                "ne_type",
                "ne_name",
                "alarm_name",
                "location",
                "count",
                "first_time",
                "latest_time",
                "ack_time",
                "clear_time",
            ],
            "rows": [
                {
                    "severity": "major",
                    "ne_name": "10.0.0.1",
                    "alarm_name": "old",
                    "location": "old-loc",
                    "count": "1",
                    "first_time": "2026-04-27 01:59:59",
                    "latest_time": "2026-04-27 01:59:59",
                },
                {
                    "severity": "critical",
                    "ne_name": "10.0.0.2",
                    "alarm_name": "new",
                    "location": "new-loc",
                    "count": "2",
                    "first_time": "2026-04-27 02:00:01",
                    "latest_time": "2026-04-27 02:00:02",
                },
            ],
        }
        (output_dir / "activity_alarm.json").write_text(json.dumps(table), encoding="utf-8")
        (output_dir / "history_alarm.json").write_text(json.dumps(table), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(ems_alarm.subprocess, "run", fake_run)

    result = ems_alarm.run_ems_alarm_query(
        "wf-demo",
        "20260427",
        log_dir=str(tmp_path),
        since_time=datetime(2026, 4, 27, 10, 0, 0),
    )

    assert calls[0]["cmd"] == [
        "python-test",
        "-m",
        "ems_automation.cli",
        "alarm-read",
        "--target",
        "all",
        "--output-dir",
        str(output_dir),
    ]
    assert calls[0]["cwd"] == str(ems_dir)
    assert calls[0]["timeout"] == 33
    assert result["counts"] == {"activity": 1, "history": 2}
    assert result["summary_path"] == str(output_dir / "alarm_export_summary.json")
    assert result["recent_alarms"]["since_time_local"] == "2026-04-27 10:00:00"
    assert result["recent_alarms"]["since_time_ems"] == "2026-04-27 02:00:00"
    assert [row["alarm_name"] for row in result["recent_alarms"]["activity"]] == ["new"]
    assert [row["alarm_name"] for row in result["recent_alarms"]["history"]] == ["new"]


def test_format_recent_alarm_log_lines_includes_activity_and_history():
    result = {
        "recent_alarms": {
            "since_time_ems": "2026-04-27 02:00:00",
            "since_time_local": "2026-04-27 10:00:00",
            "ems_timezone_offset_hours": 0,
            "activity": [
                {
                    "event_time": "2026-04-27 10:00:02",
                    "severity": "critical",
                    "ne_name": "10.0.0.2",
                    "alarm_name": "new-active",
                    "location": "pod-a",
                    "count": "1",
                    "first_time": "2026-04-27 10:00:01",
                    "latest_time": "2026-04-27 10:00:02",
                    "clear_time": "",
                }
            ],
            "history": [],
        }
    }

    lines = ems_alarm.format_recent_alarm_log_lines(result)

    assert lines[0] == "[EMS] alarms since case_start_ems=2026-04-27 02:00:00 case_start_local=2026-04-27 10:00:00 ems_tz=UTC+0 activity=1 history=0"
    assert "alarm=new-active" in lines[2]
    assert lines[-1] == "[EMS] history alarms since case start: none"


def test_run_ems_alarm_query_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "EMS_ALARM_ENABLED", False)

    result = ems_alarm.run_ems_alarm_query("wf-demo", "20260427")

    assert result["skipped"] is True
    assert result["reason"] == "EMS_ALARM_ENABLED is false"


def test_run_ems_login_check_invokes_auth_check(tmp_path, monkeypatch):
    ems_dir = tmp_path / "ems_automation"
    ems_dir.mkdir()

    monkeypatch.setattr(config, "EMS_LOGIN_ENABLED", True)
    monkeypatch.setattr(config, "EMS_LOGIN_TIMEOUT_SECONDS", 44)
    monkeypatch.setattr(config, "EMS_ALARM_ENABLED", True)
    monkeypatch.setattr(config, "EMS_ALARM_PYTHON", "python-test")
    monkeypatch.setattr(config, "EMS_AUTOMATION_DIR", str(ems_dir))

    calls = []

    def fake_run(cmd, cwd=None, env=None, stdout=None, stderr=None, text=None, encoding=None, errors=None, timeout=None):
        calls.append(
            {
                "cmd": cmd,
                "cwd": cwd,
                "timeout": timeout,
                "env": env,
            }
        )
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({"status": "ok", "storage_state": "state.json", "url": "https://ems/"}),
            stderr="",
        )

    monkeypatch.setattr(ems_alarm.subprocess, "run", fake_run)

    result = ems_alarm.run_ems_login_check()

    assert calls[0]["cmd"] == ["python-test", "-m", "ems_automation.cli", "auth-check"]
    assert calls[0]["cwd"] == str(ems_dir)
    assert calls[0]["timeout"] == 44
    assert result["status"] == "ok"
    assert result["storage_state"] == "state.json"


def test_run_ems_login_check_skips_when_alarm_disabled(monkeypatch):
    monkeypatch.setattr(config, "EMS_LOGIN_ENABLED", True)
    monkeypatch.setattr(config, "EMS_ALARM_ENABLED", False)

    result = ems_alarm.run_ems_login_check()

    assert result["skipped"] is True
    assert result["reason"] == "EMS_ALARM_ENABLED is false"


def test_run_ems_manual_login_invokes_auth_login(tmp_path, monkeypatch):
    ems_dir = tmp_path / "ems_automation"
    ems_dir.mkdir()

    monkeypatch.setattr(config, "EMS_LOGIN_ENABLED", True)
    monkeypatch.setattr(config, "EMS_LOGIN_MANUAL_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(config, "EMS_ALARM_ENABLED", True)
    monkeypatch.setattr(config, "EMS_ALARM_PYTHON", "python-test")
    monkeypatch.setattr(config, "EMS_AUTOMATION_DIR", str(ems_dir))

    calls = []

    class FakeStdio:
        def __enter__(self):
            return {"stdin": "stdin", "stdout": "stdout", "stderr": "stderr"}

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_run(cmd, cwd=None, env=None, timeout=None, **kwargs):
        calls.append({"cmd": cmd, "cwd": cwd, "timeout": timeout, "kwargs": kwargs})
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ems_alarm, "_interactive_subprocess_stdio", lambda: FakeStdio())
    monkeypatch.setattr(ems_alarm.subprocess, "run", fake_run)

    result = ems_alarm.run_ems_manual_login()

    assert calls[0]["cmd"] == ["python-test", "-m", "ems_automation.cli", "auth-login"]
    assert calls[0]["cwd"] == str(ems_dir)
    assert calls[0]["timeout"] is None
    assert calls[0]["kwargs"] == {"stdin": "stdin", "stdout": "stdout", "stderr": "stderr"}
    assert result["rc"] == 0
