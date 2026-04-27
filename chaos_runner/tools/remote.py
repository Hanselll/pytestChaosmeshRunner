# -*- coding: utf-8 -*-
import posixpath
import shlex
import subprocess
import time
from datetime import datetime

from chaos_runner import config


def _execution_mode():
    return str(getattr(config, "EXECUTION_MODE", "local") or "local").strip().lower()


def is_remote_apply_enabled():
    return _execution_mode() == "remote_apply"


def _ssh_target():
    host = str(getattr(config, "REMOTE_APPLY_HOST", "") or "").strip()
    if not host:
        raise RuntimeError("REMOTE_APPLY_HOST is required for remote_apply mode")
    user = str(getattr(config, "REMOTE_APPLY_USER", "") or "").strip()
    return "{}@{}".format(user, host) if user else host


def _ssh_args():
    args = ["ssh"]
    extra = str(getattr(config, "REMOTE_APPLY_SSH_EXTRA_OPTS", "") or "").strip()
    if extra:
        args.extend(shlex.split(extra))
    args.extend(["-p", str(int(getattr(config, "REMOTE_APPLY_PORT", 22) or 22)), _ssh_target()])
    return args


def run_remote_command(remote_cmd, check=True, input_text=None, timeout_seconds=None):
    timeout_value = timeout_seconds
    if timeout_value is None:
        timeout_value = getattr(config, "REMOTE_COMMAND_TIMEOUT_SECONDS", 60)
    timeout = int(timeout_value or 0)
    try:
        proc = subprocess.run(
            _ssh_args() + [remote_cmd],
            input=input_text,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout if timeout > 0 else None,
        )
    except subprocess.TimeoutExpired:
        stderr = "Remote command timed out after {}s".format(timeout)
        if check:
            raise RuntimeError(
                "Remote command failed: {}\nrc={}\nstdout:\n{}\nstderr:\n{}\n".format(
                    remote_cmd,
                    124,
                    "",
                    stderr,
                )
            )
        return {"rc": 124, "stdout": "", "stderr": stderr, "command": remote_cmd}
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if check and proc.returncode != 0:
        raise RuntimeError(
            "Remote command failed: {}\nrc={}\nstdout:\n{}\nstderr:\n{}\n".format(
                remote_cmd,
                proc.returncode,
                stdout,
                stderr,
            )
        )
    return {"rc": proc.returncode, "stdout": stdout, "stderr": stderr, "command": remote_cmd}


def build_remote_workflow_path(wf_name):
    base_dir = str(getattr(config, "REMOTE_APPLY_BASE_DIR", "/tmp/chaos-runner") or "/tmp/chaos-runner").strip()
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in str(wf_name or "wf"))
    safe_name = safe_name.strip("-") or "wf"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return posixpath.join(base_dir, "{}_{}.yaml".format(safe_name, ts))


def upload_text(remote_path, text):
    remote_dir = posixpath.dirname(remote_path)
    remote_cmd = "umask 077 && mkdir -p {d} && cat > {p}".format(
        d=shlex.quote(remote_dir),
        p=shlex.quote(remote_path),
    )
    return run_remote_command(remote_cmd, check=True, input_text=text)


def kubectl_apply_remote(remote_path):
    retries = max(0, int(getattr(config, "REMOTE_APPLY_RETRIES", 2) or 0))
    retry_delay = max(0.0, float(getattr(config, "REMOTE_APPLY_RETRY_DELAY_SECONDS", 2) or 0))
    remote_cmd = "kubectl apply -f {}".format(shlex.quote(remote_path))
    attempt = 0
    last_error = None

    while attempt <= retries:
        attempt += 1
        try:
            result = run_remote_command(remote_cmd, check=True)
            result["remote_path"] = remote_path
            result["attempt"] = attempt
            return result
        except RuntimeError as exc:
            last_error = exc
            if attempt > retries:
                raise
            if retry_delay > 0:
                time.sleep(retry_delay)

    raise last_error


def kubectl_delete_workflow_remote(namespace, name):
    wait_arg = "" if bool(getattr(config, "WORKFLOW_DELETE_WAIT", False)) else " --wait=false"
    return run_remote_command(
        "kubectl -n {} delete workflow {} --ignore-not-found{}".format(
            shlex.quote(namespace),
            shlex.quote(name),
            wait_arg,
        ),
        check=False,
    )
