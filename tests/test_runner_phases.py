# -*- coding: utf-8 -*-
from types import SimpleNamespace

from chaos_runner import config
from chaos_runner import runner
from chaos_runner import yaml_compat as yaml
from chaos_runner.executor import observer


def test_phase_case_splits_network_and_kill_faults():
    case = {
        "name": "demo",
        "workflow": {"name": "wf-demo", "namespace": "default"},
        "renderer": "modular_chaos",
        "stages": [
            {
                "mode": "parallel",
                "faults": [
                    {"type": "network_delay", "selectors": {"from": "a", "to": "b"}},
                    {"type": "pod_kill", "target": "x"},
                    {"type": "network_loss", "selectors": {"from": "a", "to": "b"}},
                ],
            }
        ],
    }

    network_case = runner._phase_case(case, "network", "net")
    kill_case = runner._phase_case(case, "kill", "kill")

    assert [item["type"] for item in network_case["stages"][0]["faults"]] == ["network_delay", "network_loss"]
    assert [item["type"] for item in kill_case["stages"][0]["faults"]] == ["pod_kill"]
    assert network_case["workflow"]["name"] == "wf-demo-net"
    assert kill_case["workflow"]["name"] == "wf-demo-kill"


def test_execute_case_from_args_runs_network_verify_before_kill_wait(tmp_path, monkeypatch):
    case_path = tmp_path / "case.yaml"
    case_path.write_text(
        "\n".join(
            [
                "name: phased",
                "workflow:",
                "  name: wf-phased",
                "  namespace: default",
                "renderer: modular_chaos",
                "targets: []",
                "faults:",
                "  - type: network_delay",
                "    selectors: {from: a, to: b}",
                "  - type: pod_kill",
                "    target: x",
                "wait_seconds: 7",
                "cleanup: true",
            ]
        ),
        encoding="utf-8",
    )

    calls = []
    applied_network_yaml = {}

    monkeypatch.setattr(config, "load", lambda config_file="": None)
    monkeypatch.setattr(config, "validate_case_dependencies", lambda case: None)
    monkeypatch.setattr(config, "WF_NAMESPACE", "default")
    monkeypatch.setattr(config, "NS_TARGET", "ns-demo")
    monkeypatch.setattr(config, "DEFAULT_WAIT_SECONDS", 25)
    monkeypatch.setattr(config, "DELETE_WORKFLOW_AFTER", True)
    monkeypatch.setattr(config, "NET_VERIFY_TIMEOUT_SECONDS", 11)
    monkeypatch.setattr(config, "NETWORK_PHASE_EXTRA_BUFFER_SECONDS", 5)
    monkeypatch.setattr(config, "to_dict", lambda mask_secrets=False: {"ACTIVE_CONFIG_FILE": ""})

    monkeypatch.setattr(
        runner,
        "build",
        lambda case, cfg: (
            "\n".join(
                [
                    "apiVersion: chaos-mesh.org/v1alpha1",
                    "kind: Workflow",
                    "spec:",
                    "  templates: []",
                ]
            ),
            {"target-a": {"pod": "pod-a"}},
        ),
    )

    def fake_render_phase_yaml(case, resolved):
        if case is None:
            return "", None
        wf_name = (case.get("workflow") or {}).get("name") or case.get("name")
        if wf_name.endswith("-net"):
            return "\n".join(
                [
                    "apiVersion: chaos-mesh.org/v1alpha1",
                    "kind: Workflow",
                    "spec:",
                    "  templates:",
                    "    - name: f0-net-delay",
                    "      templateType: NetworkChaos",
                    "      networkChaos:",
                    "        action: delay",
                    "        mode: all",
                    "        selector: {}",
                    "        delay:",
                    "          latency: 300ms",
                ]
            ), wf_name
        if wf_name.endswith("-kill"):
            return "kill-yaml", wf_name
        return "", wf_name

    monkeypatch.setattr(runner, "_render_phase_yaml", fake_render_phase_yaml)
    monkeypatch.setattr(runner, "expand_network_chaos_to_component_pods", lambda text, ns: text)
    def fake_apply_workflow_once(path, yaml_text, wf_ns, wf_name):
        calls.append(("apply", wf_name))
        applied_network_yaml[wf_name] = yaml_text
        return {"execution_mode": "local", "yaml_path": path, "apply_result": "ok"}

    monkeypatch.setattr(runner, "_apply_workflow_once", fake_apply_workflow_once)
    monkeypatch.setattr(runner, "verify_network_chaos_before_kill", lambda ns, text, timeout, loggers=None: calls.append(("verify", timeout)) or {"verdict": "PASS"})
    monkeypatch.setattr(runner, "run_workflow", lambda path, wf_ns, wf_name, wait_seconds, cleanup=True, during_wait=None, yaml_text=None: calls.append(("kill", wf_name, wait_seconds, cleanup)) or {"execution_mode": "local", "yaml_path": path, "apply_result": "ok", "delete_result": "ok"})
    monkeypatch.setattr(runner, "_delete_workflow_once", lambda wf_ns, wf_name: calls.append(("cleanup", wf_name)) or "deleted")
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: calls.append(("sleep", seconds)))

    monkeypatch.setattr(observer, "extract_podchaos_target_pods", lambda wf_yaml_text, namespace: ["pod-a"])
    monkeypatch.setattr(observer, "extract_target_pods_from_resolved", lambda resolved: ["pod-a"])
    monkeypatch.setattr(observer, "resolve_lmt_commands", lambda observer_cfg, phase, mode: [])
    monkeypatch.setattr(observer, "_collect_pre_common_state", lambda namespace, podchaos_target_pods, role_source_pods: {"workflow_name": "", "target_pods": [], "role_source_pods": [], "pre_pod_status": {}, "run_start": None})
    monkeypatch.setattr(observer, "_collect_post_common_state", lambda namespace, pre_state: {"pod_status": {}, "post_display_map": {}, "replacements": {}, "runtime_events": [], "runtime_logs": []})
    monkeypatch.setattr(observer, "collect_pre_case_state", lambda namespace, podchaos_target_pods, role_source_pods, case_log, lmt_mode="table", common_state=None, lmt_commands=None: dict(common_state or {}))
    monkeypatch.setattr(observer, "collect_post_case_state", lambda namespace, pre_state, case_log, lmt_mode="table", common_state=None, lmt_commands=None: None)

    args = SimpleNamespace(case=str(case_path), config_file="", dry_run=False, out="", log_dir=str(tmp_path / "logs"))
    result = runner.execute_case_from_args(args, echo_stdout=False)

    assert calls[:4] == [
        ("apply", "wf-phased-net"),
        ("verify", 11),
        ("kill", "wf-phased-kill", 7, True),
        ("cleanup", "wf-phased-net"),
    ]
    assert "deadline: 23s" in applied_network_yaml["wf-phased-net"]
    assert result["network_execution_result"]["execution_mode"] == "local"
    assert result["kill_execution_result"]["execution_mode"] == "local"


def test_rewrite_network_chaos_deadlines_updates_all_network_templates():
    text = "\n".join(
        [
            "apiVersion: chaos-mesh.org/v1alpha1",
            "kind: Workflow",
            "spec:",
            "  templates:",
            "    - name: f0-net-delay",
            "      templateType: NetworkChaos",
            "      deadline: 30s",
            "      networkChaos: {action: delay}",
            "    - name: wait-1",
            "      templateType: Suspend",
            "      deadline: 5s",
            "    - name: f1-net-loss",
            "      templateType: NetworkChaos",
            "      networkChaos: {action: loss}",
        ]
    )

    updated = runner._rewrite_network_chaos_deadlines(text, 42)
    doc = yaml.safe_load(updated)
    templates = ((doc.get("spec") or {}).get("templates") or [])
    deadlines = [tpl.get("deadline") for tpl in templates if isinstance(tpl, dict) and tpl.get("templateType") == "NetworkChaos"]
    suspend_deadlines = [tpl.get("deadline") for tpl in templates if isinstance(tpl, dict) and tpl.get("templateType") == "Suspend"]

    assert deadlines == ["42s", "42s"]
    assert suspend_deadlines == ["5s"]


def test_rewrite_network_chaos_deadlines_preserves_loss_fields_as_strings():
    text = "\n".join(
        [
            "apiVersion: chaos-mesh.org/v1alpha1",
            "kind: Workflow",
            "spec:",
            "  templates:",
            "    - name: f1-net-loss",
            "      templateType: NetworkChaos",
            "      deadline: 30s",
            "      networkChaos:",
            "        action: loss",
            "        loss:",
            "          loss: 10",
            "          correlation: 0",
        ]
    )

    updated = runner._rewrite_network_chaos_deadlines(text, 42)
    doc = yaml.safe_load(updated)
    templates = ((doc.get("spec") or {}).get("templates") or [])
    loss_tpl = next(tpl for tpl in templates if isinstance(tpl, dict) and tpl.get("templateType") == "NetworkChaos")
    loss_spec = ((loss_tpl.get("networkChaos") or {}).get("loss") or {})

    assert loss_tpl.get("deadline") == "42s"
    assert loss_spec.get("loss") == "10"
    assert loss_spec.get("correlation") == "0"
    assert isinstance(loss_spec.get("loss"), str)
    assert isinstance(loss_spec.get("correlation"), str)


def test_execute_case_from_args_does_not_cleanup_network_when_verify_raises(tmp_path, monkeypatch):
    case_path = tmp_path / "case.yaml"
    case_path.write_text(
        "\n".join(
            [
                "name: phased",
                "workflow:",
                "  name: wf-phased",
                "  namespace: default",
                "renderer: modular_chaos",
                "targets: []",
                "faults:",
                "  - type: network_delay",
                "    selectors: {from: a, to: b}",
                "  - type: pod_kill",
                "    target: x",
                "wait_seconds: 7",
                "cleanup: true",
            ]
        ),
        encoding="utf-8",
    )

    calls = []

    monkeypatch.setattr(config, "load", lambda config_file="": None)
    monkeypatch.setattr(config, "validate_case_dependencies", lambda case: None)
    monkeypatch.setattr(config, "WF_NAMESPACE", "default")
    monkeypatch.setattr(config, "NS_TARGET", "ns-demo")
    monkeypatch.setattr(config, "DEFAULT_WAIT_SECONDS", 25)
    monkeypatch.setattr(config, "DELETE_WORKFLOW_AFTER", True)
    monkeypatch.setattr(config, "NET_VERIFY_TIMEOUT_SECONDS", 11)
    monkeypatch.setattr(config, "NETWORK_PHASE_EXTRA_BUFFER_SECONDS", 5)
    monkeypatch.setattr(config, "to_dict", lambda mask_secrets=False: {"ACTIVE_CONFIG_FILE": ""})

    monkeypatch.setattr(
        runner,
        "build",
        lambda case, cfg: (
            "\n".join(
                [
                    "apiVersion: chaos-mesh.org/v1alpha1",
                    "kind: Workflow",
                    "spec:",
                    "  templates: []",
                ]
            ),
            {"target-a": {"pod": "pod-a"}},
        ),
    )

    def fake_render_phase_yaml(case, resolved):
        if case is None:
            return "", None
        wf_name = (case.get("workflow") or {}).get("name") or case.get("name")
        if wf_name.endswith("-net"):
            return "\n".join(
                [
                    "apiVersion: chaos-mesh.org/v1alpha1",
                    "kind: Workflow",
                    "spec:",
                    "  templates:",
                    "    - name: f0-net-delay",
                    "      templateType: NetworkChaos",
                    "      networkChaos:",
                    "        action: delay",
                    "        mode: all",
                    "        selector: {}",
                    "        delay:",
                    "          latency: 300ms",
                ]
            ), wf_name
        if wf_name.endswith("-kill"):
            return "kill-yaml", wf_name
        return "", wf_name

    monkeypatch.setattr(runner, "_render_phase_yaml", fake_render_phase_yaml)
    monkeypatch.setattr(runner, "expand_network_chaos_to_component_pods", lambda text, ns: text)
    monkeypatch.setattr(
        runner,
        "_apply_workflow_once",
        lambda path, yaml_text, wf_ns, wf_name: calls.append(("apply", wf_name)) or {"execution_mode": "local", "yaml_path": path, "apply_result": "ok"},
    )
    monkeypatch.setattr(
        runner,
        "verify_network_chaos_before_kill",
        lambda ns, text, timeout, loggers=None: (_ for _ in ()).throw(RuntimeError("verify boom")),
    )
    monkeypatch.setattr(
        runner,
        "run_workflow",
        lambda path, wf_ns, wf_name, wait_seconds, cleanup=True, during_wait=None, yaml_text=None: calls.append(("kill", wf_name)),
    )
    monkeypatch.setattr(runner, "_delete_workflow_once", lambda wf_ns, wf_name: calls.append(("cleanup", wf_name)) or "deleted")

    monkeypatch.setattr(observer, "extract_podchaos_target_pods", lambda wf_yaml_text, namespace: ["pod-a"])
    monkeypatch.setattr(observer, "extract_target_pods_from_resolved", lambda resolved: ["pod-a"])
    monkeypatch.setattr(observer, "resolve_lmt_commands", lambda observer_cfg, phase, mode: [])
    monkeypatch.setattr(observer, "_collect_pre_common_state", lambda namespace, podchaos_target_pods, role_source_pods: {"workflow_name": "", "target_pods": [], "role_source_pods": [], "pre_pod_status": {}, "run_start": None})
    monkeypatch.setattr(observer, "_collect_post_common_state", lambda namespace, pre_state: {"pod_status": {}, "post_display_map": {}, "replacements": {}, "runtime_events": [], "runtime_logs": []})
    monkeypatch.setattr(observer, "collect_pre_case_state", lambda namespace, podchaos_target_pods, role_source_pods, case_log, lmt_mode="table", common_state=None, lmt_commands=None: dict(common_state or {}))
    monkeypatch.setattr(observer, "collect_post_case_state", lambda namespace, pre_state, case_log, lmt_mode="table", common_state=None, lmt_commands=None: None)

    args = SimpleNamespace(case=str(case_path), config_file="", dry_run=False, out="", log_dir=str(tmp_path / "logs"))

    try:
        runner.execute_case_from_args(args, echo_stdout=False)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert str(exc) == "verify boom"

    assert calls == [("apply", "wf-phased-net")]
