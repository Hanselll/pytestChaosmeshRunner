# -*- coding: utf-8 -*-
import json
import os, time, re, subprocess

from chaos_runner import config
from chaos_runner.tools.remote import run_remote_command

try:
    import fcntl
    import pty
    import select
except ModuleNotFoundError:
    fcntl = None
    pty = None
    select = None


def _require_posix_pty():
    if pty is None or fcntl is None or select is None:
        raise RuntimeError("interactive LMT PTY execution requires POSIX pty/fcntl support")

def set_nonblocking(fd):
    _require_posix_pty()
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

def read_until(fd, pattern, timeout=10):
    _require_posix_pty()
    buf=""
    end=time.time()+timeout
    while time.time()<end:
        r,_,_=select.select([fd],[],[],0.5)
        if not r:
            continue
        try:
            data=os.read(fd,4096)
        except BlockingIOError:
            continue
        except OSError:
            break
        if not data:
            break
        buf += data.decode(errors="ignore")
        if re.search(pattern, buf):
            break
    return buf

def extract_ip(text):
    m=re.search(r'addr\\?"\s*:\s*\\?"((?:\d{1,3}\.){3}\d{1,3})', text)
    if m: return m.group(1)
    m2=re.search(r'((?:\d{1,3}\.){3}\d{1,3})', text)
    if m2: return m2.group(1)
    return None


def _tail_compact(text, limit=12):
    lines = []
    for line in (text or "").splitlines():
        s = (line or "").rstrip()
        if not s:
            continue
        lines.append(s)
    return "\n".join(lines[-limit:])


def _build_login_cmd(username, login_ip, login_port, login_mode):
    mode = str(login_mode or "explicit").strip().lower()
    if mode == "discover_in_pod":
        return "lmt-cli login --username {}\n".format(username)
    return "lmt-cli login --ip {} --port {} --username {}\n".format(login_ip, login_port, username)

def run_lmt_list_in_container(namespace, pod, container, login_ip, login_port, username, password, table, raw_out_path="/tmp/lmt_raw_pty.txt", login_mode="explicit"):
    _require_posix_pty()
    master_fd, slave_fd = pty.openpty()
    set_nonblocking(master_fd)

    cmd=["kubectl","exec","-it","-n",namespace,pod]
    if str(container or "").strip():
        cmd.extend(["-c", str(container).strip()])
    cmd.extend(["--","bash","--noprofile","--norc"])
    proc=subprocess.Popen(cmd, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True)
    os.close(slave_fd)

    def send(s): os.write(master_fd, s.encode())

    out=read_until(master_fd, r".*", timeout=1)
    send("echo __READY__\n")
    out += read_until(master_fd, r"__READY__", timeout=8)
    if "__READY__" not in out:
        open(raw_out_path,"w").write(out)
        raise RuntimeError("no __READY__ raw={}".format(raw_out_path))

    send("export HOME=/tmp\n")
    out += read_until(master_fd, r".*", timeout=1)
    # disable terminal echo to avoid marker text being echoed before command finishes
    send("stty -echo\n")
    out += read_until(master_fd, r".*", timeout=1)
    send("export PS1='__PROMPT__# '\n")
    out += read_until(master_fd, r"__PROMPT__#", timeout=3)
    # avoid echoed input lines polluting parsed command output
    send("stty -echo\n")
    out += read_until(master_fd, r".*", timeout=1)

    send(_build_login_cmd(username, login_ip, login_port, login_mode))
    out += read_until(master_fd, r"(Enter Password:|Password:)", timeout=8)
    send(password+"\n")
    out += read_until(master_fd, r"(login success|login failed|status:)", timeout=10)
    if "login success" not in out:
        open(raw_out_path,"w").write(out)
        raise RuntimeError("lmt login failed raw={}\n{}".format(raw_out_path, _tail_compact(out)))

    send("lmt-cli list {}\n".format(table))
    out += read_until(master_fd, r"(addr|records|totalItems|Error:)", timeout=10)

    send("exit\n"); send("exit\n")
    time.sleep(0.2)
    proc.terminate()
    try: os.close(master_fd)
    except Exception: pass
    open(raw_out_path,"w").write(out)
    return out


def run_lmt_commands_in_container(
    namespace,
    pod,
    container,
    login_ip,
    login_port,
    username,
    password,
    commands,
    raw_out_path="/tmp/lmt_raw_pty_multi.txt",
    login_mode="explicit",
):
    """Login once then execute commands in interactive PTY.

    Returns:
        {
          "raw_output": "...",
          "results": [{"command": "...", "output": "..."}, ...]
        }
    """
    _require_posix_pty()
    master_fd, slave_fd = pty.openpty()
    set_nonblocking(master_fd)

    cmd = ["kubectl", "exec", "-it", "-n", namespace, pod]
    if str(container or "").strip():
        cmd.extend(["-c", str(container).strip()])
    cmd.extend(["--", "bash", "--noprofile", "--norc"])
    proc = subprocess.Popen(cmd, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True)
    os.close(slave_fd)

    def send(s):
        os.write(master_fd, s.encode())

    out = read_until(master_fd, r".*", timeout=1)
    send("echo __READY__\n")
    out += read_until(master_fd, r"__READY__", timeout=8)
    if "__READY__" not in out:
        open(raw_out_path, "w").write(out)
        raise RuntimeError("no __READY__ raw={}".format(raw_out_path))

    send("export HOME=/tmp\n")
    out += read_until(master_fd, r".*", timeout=1)
    send("stty -echo\n")
    out += read_until(master_fd, r".*", timeout=1)
    send("export PS1='__PROMPT__# '\n")
    out += read_until(master_fd, r"__PROMPT__#", timeout=3)

    send(_build_login_cmd(username, login_ip, login_port, login_mode))
    out += read_until(master_fd, r"(Enter Password:|Password:)", timeout=8)
    send(password + "\n")
    out += read_until(master_fd, r"(login success|login failed|status:)", timeout=10)
    if "login success" not in out:
        open(raw_out_path, "w").write(out)
        raise RuntimeError("lmt login failed raw={}\n{}".format(raw_out_path, _tail_compact(out)))

    results = []
    for idx, command in enumerate(commands):
        begin = "__CMD_BEGIN_{}__".format(idx)
        end = "__CMD_DONE_{}__".format(idx)
        send("printf '{b}\\n'; {cmd}; printf '\\n{e}\\n'\n".format(b=begin, cmd=command, e=end))
        # LMT table output may take longer in busy env; use larger timeout.
        chunk = read_until(master_fd, re.escape(end), timeout=90)
        out += chunk

        # Prefer parsing the current chunk first; fallback to accumulated output.
        ei = chunk.rfind(end)
        bi = chunk.rfind(begin, 0, ei if ei >= 0 else None)
        seg = ""
        if bi >= 0 and ei > bi:
            seg = chunk[bi + len(begin):ei]
        else:
            ei2 = out.rfind(end)
            bi2 = out.rfind(begin, 0, ei2 if ei2 >= 0 else None)
            if bi2 >= 0 and ei2 > bi2:
                seg = out[bi2 + len(begin):ei2]
            elif ei >= 0:
                seg = chunk[:ei]
        results.append({"command": command, "output": seg.strip()})

    send("exit\n")
    send("exit\n")
    time.sleep(0.2)
    proc.terminate()
    try:
        os.close(master_fd)
    except Exception:
        pass
    open(raw_out_path, "w").write(out)
    return {"raw_output": out, "results": results}


def run_lmt_commands_in_container_remote(
    namespace,
    pod,
    container,
    login_ip,
    login_port,
    username,
    password,
    commands,
    raw_out_path="/tmp/lmt_raw_pty_multi.txt",
    login_mode="explicit",
):
    commands = list(commands or [])
    payload = {
        "namespace": namespace,
        "pod": pod,
        "container": container,
        "login_ip": login_ip,
        "login_port": login_port,
        "username": username,
        "password": password,
        "commands": commands,
        "raw_out_path": raw_out_path,
        "login_mode": login_mode,
    }
    payload_json = json.dumps(payload, ensure_ascii=True)
    remote_script = """import json, os, time, re, subprocess, sys
import fcntl
import pty
import select

PAYLOAD = json.loads({payload_json!r})

def set_nonblocking(fd):
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

def read_until(fd, pattern, timeout=10):
    buf = ""
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.5)
        if not r:
            continue
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            continue
        except OSError:
            break
        if not data:
            break
        buf += data.decode(errors="ignore")
        if re.search(pattern, buf):
            break
    return buf

def tail_compact(text, limit=12):
    lines = []
    for line in (text or "").splitlines():
        s = (line or "").rstrip()
        if s:
            lines.append(s)
    return "\\n".join(lines[-limit:])

def build_login_cmd(username, login_ip, login_port, login_mode):
    mode = str(login_mode or "explicit").strip().lower()
    if mode == "discover_in_pod":
        return "lmt-cli login --username {{}}\\n".format(username)
    return "lmt-cli login --ip {{}} --port {{}} --username {{}}\\n".format(login_ip, login_port, username)

def run():
    master_fd, slave_fd = pty.openpty()
    set_nonblocking(master_fd)

    cmd = ["kubectl", "exec", "-it", "-n", PAYLOAD["namespace"], PAYLOAD["pod"]]
    if str(PAYLOAD.get("container") or "").strip():
        cmd.extend(["-c", str(PAYLOAD.get("container")).strip()])
    cmd.extend(["--", "bash", "--noprofile", "--norc"])
    proc = subprocess.Popen(cmd, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True)
    os.close(slave_fd)

    def send(s):
        os.write(master_fd, s.encode())

    out = read_until(master_fd, r".*", timeout=1)
    send("echo __READY__\\n")
    out += read_until(master_fd, r"__READY__", timeout=8)
    if "__READY__" not in out:
        open(PAYLOAD["raw_out_path"], "w").write(out)
        raise RuntimeError("no __READY__ raw={{}}".format(PAYLOAD["raw_out_path"]))

    send("export HOME=/tmp\\n")
    out += read_until(master_fd, r".*", timeout=1)
    send("stty -echo\\n")
    out += read_until(master_fd, r".*", timeout=1)
    send("export PS1='__PROMPT__# '\\n")
    out += read_until(master_fd, r"__PROMPT__#", timeout=3)

    send(build_login_cmd(PAYLOAD["username"], PAYLOAD["login_ip"], PAYLOAD["login_port"], PAYLOAD.get("login_mode")))
    out += read_until(master_fd, r"(Enter Password:|Password:)", timeout=8)
    send(PAYLOAD["password"] + "\\n")
    out += read_until(master_fd, r"(login success|login failed|status:)", timeout=10)
    if "login success" not in out:
        open(PAYLOAD["raw_out_path"], "w").write(out)
        raise RuntimeError("lmt login failed raw={{}}\\n{{}}".format(PAYLOAD["raw_out_path"], tail_compact(out)))

    results = []
    for idx, command in enumerate(PAYLOAD.get("commands") or []):
        begin = "__CMD_BEGIN_{{}}__".format(idx)
        end = "__CMD_DONE_{{}}__".format(idx)
        send("printf '{{}}\\\\n'; {{}}; printf '\\\\n{{}}\\\\n'\\n".format(begin, command, end))
        chunk = read_until(master_fd, re.escape(end), timeout=90)
        out += chunk

        ei = chunk.rfind(end)
        bi = chunk.rfind(begin, 0, ei if ei >= 0 else None)
        seg = ""
        if bi >= 0 and ei > bi:
            seg = chunk[bi + len(begin):ei]
        else:
            ei2 = out.rfind(end)
            bi2 = out.rfind(begin, 0, ei2 if ei2 >= 0 else None)
            if bi2 >= 0 and ei2 > bi2:
                seg = out[bi2 + len(begin):ei2]
            elif ei >= 0:
                seg = chunk[:ei]
        results.append({{"command": command, "output": seg.strip()}})

    send("exit\\n")
    send("exit\\n")
    time.sleep(0.2)
    proc.terminate()
    try:
        os.close(master_fd)
    except Exception:
        pass
    open(PAYLOAD["raw_out_path"], "w").write(out)
    return {{"raw_output": out, "results": results}}

try:
    result = run()
    sys.stdout.write(json.dumps({{"ok": True, "result": result}}, ensure_ascii=True))
except Exception as exc:
    sys.stdout.write(json.dumps({{"ok": False, "error": str(exc)}}, ensure_ascii=True))
    sys.exit(1)
""".format(payload_json=payload_json)

    configured_timeout = int(getattr(config, "LMT_REMOTE_TIMEOUT_SECONDS", 900) or 0)
    command_timeout = 60 + (max(1, len(commands)) * 95)
    timeout_seconds = max(configured_timeout, command_timeout)
    result = run_remote_command(
        "python3 -",
        check=True,
        input_text=remote_script,
        timeout_seconds=timeout_seconds,
    )
    try:
        parsed = json.loads(result.get("stdout", ""))
    except Exception as exc:
        raise RuntimeError("failed to parse remote LMT helper output: {}".format(exc))
    if not parsed.get("ok"):
        raise RuntimeError(parsed.get("error") or "remote LMT helper failed")
    return parsed.get("result") or {"raw_output": "", "results": []}
