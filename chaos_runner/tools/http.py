# -*- coding: utf-8 -*-
import json
import shlex
import socket
import urllib.error
import urllib.request

from chaos_runner.tools.remote import is_remote_apply_enabled, run_remote_command


def http_get_json(url, timeout=5):
    if is_remote_apply_enabled():
        cmd = "curl -sS --max-time {} {}".format(int(timeout), shlex.quote(url))
        try:
            result = run_remote_command(cmd, check=True)
        except RuntimeError as e:
            raise RuntimeError("HTTP GET failed remotely: {} err={}".format(url, e))
        body = result.get("stdout", "")
        if not body:
            raise RuntimeError("HTTP GET empty response: {}".format(url))
        try:
            return json.loads(body)
        except ValueError:
            raise RuntimeError("HTTP response is not JSON: {}".format(url))

    req = urllib.request.Request(url, headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except socket.timeout:
        raise RuntimeError("HTTP GET timeout: {} timeout={}s".format(url, timeout))
    except TimeoutError:
        raise RuntimeError("HTTP GET timeout: {} timeout={}s".format(url, timeout))
    except urllib.error.URLError as e:
        raise RuntimeError("HTTP GET failed: {} err={}".format(url, e))
    except ValueError:
        raise RuntimeError("HTTP response is not JSON: {}".format(url))
