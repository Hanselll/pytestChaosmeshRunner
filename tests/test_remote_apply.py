# -*- coding: utf-8 -*-
import subprocess

from chaos_runner import config
from chaos_runner.tools import remote


def test_kubectl_apply_remote_retries_then_succeeds(monkeypatch):
    calls = []

    monkeypatch.setattr(config, "REMOTE_APPLY_RETRIES", 2)
    monkeypatch.setattr(config, "REMOTE_APPLY_RETRY_DELAY_SECONDS", 0)

    def fake_run_remote_command(remote_cmd, check=True, input_text=None):
        calls.append(remote_cmd)
        if len(calls) < 3:
            raise RuntimeError("Remote command failed: {}".format(remote_cmd))
        return {"rc": 0, "stdout": "ok", "stderr": "", "command": remote_cmd}

    monkeypatch.setattr(remote, "run_remote_command", fake_run_remote_command)

    result = remote.kubectl_apply_remote("/tmp/chaos-runner/demo.yaml")

    assert len(calls) == 3
    assert result["rc"] == 0
    assert result["attempt"] == 3
    assert result["remote_path"] == "/tmp/chaos-runner/demo.yaml"


def test_kubectl_apply_remote_raises_after_retries(monkeypatch):
    calls = []

    monkeypatch.setattr(config, "REMOTE_APPLY_RETRIES", 1)
    monkeypatch.setattr(config, "REMOTE_APPLY_RETRY_DELAY_SECONDS", 0)

    def fake_run_remote_command(remote_cmd, check=True, input_text=None):
        calls.append(remote_cmd)
        raise RuntimeError("Remote command failed: {}".format(remote_cmd))

    monkeypatch.setattr(remote, "run_remote_command", fake_run_remote_command)

    try:
        remote.kubectl_apply_remote("/tmp/chaos-runner/demo.yaml")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "Remote command failed" in str(exc)

    assert len(calls) == 2


def test_run_remote_command_returns_timeout_when_check_false(monkeypatch):
    monkeypatch.setattr(config, "REMOTE_APPLY_HOST", "example.test")
    monkeypatch.setattr(config, "REMOTE_APPLY_PORT", 22)
    monkeypatch.setattr(config, "REMOTE_APPLY_USER", "user")
    monkeypatch.setattr(config, "REMOTE_APPLY_SSH_EXTRA_OPTS", "-T")
    monkeypatch.setattr(config, "REMOTE_COMMAND_TIMEOUT_SECONDS", 3)

    def fake_run(*args, **kwargs):
        assert kwargs["timeout"] == 3
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(remote.subprocess, "run", fake_run)

    result = remote.run_remote_command("kubectl logs demo", check=False)

    assert result["rc"] == 124
    assert "timed out after 3s" in result["stderr"]
    assert result["command"] == "kubectl logs demo"


def test_run_remote_command_raises_timeout_when_check_true(monkeypatch):
    monkeypatch.setattr(config, "REMOTE_APPLY_HOST", "example.test")
    monkeypatch.setattr(config, "REMOTE_APPLY_PORT", 22)
    monkeypatch.setattr(config, "REMOTE_APPLY_USER", "user")
    monkeypatch.setattr(config, "REMOTE_APPLY_SSH_EXTRA_OPTS", "-T")
    monkeypatch.setattr(config, "REMOTE_COMMAND_TIMEOUT_SECONDS", 3)

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(remote.subprocess, "run", fake_run)

    try:
        remote.run_remote_command("kubectl get pod", check=True)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "timed out after 3s" in str(exc)


def test_run_remote_command_allows_call_timeout_override(monkeypatch):
    monkeypatch.setattr(config, "REMOTE_APPLY_HOST", "example.test")
    monkeypatch.setattr(config, "REMOTE_APPLY_PORT", 22)
    monkeypatch.setattr(config, "REMOTE_APPLY_USER", "user")
    monkeypatch.setattr(config, "REMOTE_APPLY_SSH_EXTRA_OPTS", "-T")
    monkeypatch.setattr(config, "REMOTE_COMMAND_TIMEOUT_SECONDS", 3)

    def fake_run(*args, **kwargs):
        assert kwargs["timeout"] == 120
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(remote.subprocess, "run", fake_run)

    result = remote.run_remote_command("python3 -", check=False, timeout_seconds=120)

    assert result["rc"] == 124
    assert "timed out after 120s" in result["stderr"]


def test_kubectl_delete_workflow_remote_does_not_wait_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "WORKFLOW_DELETE_WAIT", False)

    def fake_run_remote_command(remote_cmd, check=True, input_text=None, timeout_seconds=None):
        calls.append({"cmd": remote_cmd, "check": check})
        return {"rc": 0, "stdout": "deleted", "stderr": "", "command": remote_cmd}

    monkeypatch.setattr(remote, "run_remote_command", fake_run_remote_command)

    result = remote.kubectl_delete_workflow_remote("default", "wf-demo")

    assert result["rc"] == 0
    assert calls == [
        {
            "cmd": "kubectl -n default delete workflow wf-demo --ignore-not-found --wait=false",
            "check": False,
        }
    ]


def test_kubectl_delete_workflow_remote_can_wait_when_configured(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "WORKFLOW_DELETE_WAIT", True)

    def fake_run_remote_command(remote_cmd, check=True, input_text=None, timeout_seconds=None):
        calls.append(remote_cmd)
        return {"rc": 0, "stdout": "deleted", "stderr": "", "command": remote_cmd}

    monkeypatch.setattr(remote, "run_remote_command", fake_run_remote_command)

    remote.kubectl_delete_workflow_remote("default", "wf-demo")

    assert calls == ["kubectl -n default delete workflow wf-demo --ignore-not-found"]
