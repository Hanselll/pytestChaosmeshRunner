# -*- coding: utf-8 -*-
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
