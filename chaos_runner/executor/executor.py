# -*- coding: utf-8 -*-
import time

from chaos_runner.tools.k8s import kubectl_apply, kubectl_delete_workflow
from chaos_runner.tools.remote import (
    build_remote_workflow_path,
    is_remote_apply_enabled,
    kubectl_apply_remote,
    kubectl_delete_workflow_remote,
    upload_text,
)


def run_workflow_local(yaml_path, wf_namespace, wf_name, wait_seconds, cleanup=True, during_wait=None):
    apply_result = kubectl_apply(yaml_path)
    if callable(during_wait):
        during_wait(int(wait_seconds))
    else:
        time.sleep(int(wait_seconds))

    delete_result = None
    if cleanup:
        delete_result = kubectl_delete_workflow(wf_namespace, wf_name)

    return {
        "execution_mode": "local",
        "yaml_path": yaml_path,
        "apply_result": apply_result,
        "delete_result": delete_result,
    }


def run_workflow_remote_apply(
    yaml_path,
    yaml_text,
    wf_namespace,
    wf_name,
    wait_seconds,
    cleanup=True,
    during_wait=None,
):
    remote_path = build_remote_workflow_path(wf_name)
    upload_result = upload_text(remote_path, yaml_text)
    apply_result = kubectl_apply_remote(remote_path)

    if callable(during_wait):
        during_wait(int(wait_seconds))
    else:
        time.sleep(int(wait_seconds))

    delete_result = None
    if cleanup:
        delete_result = kubectl_delete_workflow_remote(wf_namespace, wf_name)

    return {
        "execution_mode": "remote_apply",
        "yaml_path": yaml_path,
        "remote_yaml_path": remote_path,
        "upload_result": upload_result,
        "apply_result": apply_result,
        "delete_result": delete_result,
    }


def run_workflow(yaml_path, wf_namespace, wf_name, wait_seconds, cleanup=True, during_wait=None, yaml_text=None):
    if is_remote_apply_enabled():
        if yaml_text is None:
            raise RuntimeError("remote_apply mode requires rendered workflow yaml text")
        return run_workflow_remote_apply(
            yaml_path,
            yaml_text,
            wf_namespace,
            wf_name,
            wait_seconds,
            cleanup=cleanup,
            during_wait=during_wait,
        )

    return run_workflow_local(
        yaml_path,
        wf_namespace,
        wf_name,
        wait_seconds,
        cleanup=cleanup,
        during_wait=during_wait,
    )
