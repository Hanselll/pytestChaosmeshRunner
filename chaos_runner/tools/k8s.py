# -*- coding: utf-8 -*-
import json
import shlex
import subprocess

from chaos_runner.tools.remote import is_remote_apply_enabled, run_remote_command
from chaos_runner import config


def _run_local(cmd, check=True):
    proc = subprocess.run(
        cmd,
        shell=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if check and proc.returncode != 0:
        raise RuntimeError(
            "Command failed: {}\nrc={}\nstdout:\n{}\nstderr:\n{}\n".format(
                cmd,
                proc.returncode,
                out,
                err,
            )
        )
    return out


def sh(cmd, check=True):
    text = str(cmd or "").strip()
    if is_remote_apply_enabled() and (text.startswith("kubectl") or text.startswith("curl ")):
        result = run_remote_command(text, check=check)
        return result.get("stdout", "")
    return _run_local(text, check=check)


def kubectl_apply(path):
    return sh("kubectl apply -f {}".format(shlex.quote(path)))


def kubectl_delete_workflow(namespace, name):
    wait_arg = "" if bool(getattr(config, "WORKFLOW_DELETE_WAIT", False)) else " --wait=false"
    return sh(
        "kubectl -n {} delete workflow {} --ignore-not-found{}".format(
            shlex.quote(namespace),
            shlex.quote(name),
            wait_arg,
        ),
        check=False,
    )


def get_service_cluster_ip(namespace, svc_name):
    ip = sh(
        "kubectl -n {} get svc {} -o jsonpath='{{.spec.clusterIP}}'".format(
            shlex.quote(namespace),
            shlex.quote(svc_name),
        )
    ).strip().strip("'").strip('"')
    if not ip or ip.lower() == "none":
        raise RuntimeError("Service {} in {} has no clusterIP".format(svc_name, namespace))
    return ip


def get_ns_pod_ip_map(namespace):
    data = json.loads(sh("kubectl -n {} get pod -o json".format(shlex.quote(namespace))))
    out = {}
    for item in data.get("items", []):
        ip = (item.get("status") or {}).get("podIP", "")
        name = (item.get("metadata") or {}).get("name", "")
        if ip and name:
            out[ip] = name
    return out


def find_pod_by_ip_allns(ip):
    data = json.loads(sh("kubectl get pod -A -o json"))
    hits = []
    for item in data.get("items", []):
        if (item.get("status") or {}).get("podIP") == ip:
            hits.append(
                (
                    (item.get("metadata") or {}).get("namespace", ""),
                    (item.get("metadata") or {}).get("name", ""),
                    (item.get("spec") or {}).get("nodeName", ""),
                )
            )
    return hits


def exec_in_pod(namespace, pod_name, command_sh_lc):
    return sh(
        "kubectl exec -n {ns} {pod} -- sh -lc {cmd}".format(
            ns=shlex.quote(namespace),
            pod=shlex.quote(pod_name),
            cmd=shlex.quote(command_sh_lc),
        )
    )


def get_pod_ip(namespace, pod):
    out = sh(
        "kubectl -n {} get pod {} -o jsonpath='{{.status.podIP}}'".format(
            shlex.quote(namespace),
            shlex.quote(pod),
        )
    )
    return out.strip().strip("'").strip('"')
