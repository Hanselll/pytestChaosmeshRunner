# -*- coding: utf-8 -*-
from chaos_runner.runner import _extract_fault_schedule, _selector_pod_names


def test_selector_pod_names_flattens_namespace_map():
    selector = {
        "pods": {
            "default": ["pod-a", "pod-b"],
            "other": ["pod-c"],
        }
    }

    assert _selector_pod_names(selector) == ["pod-a", "pod-b", "pod-c"]


def test_extract_fault_schedule_handles_selector_pods_dict_values():
    wf_yaml = """
apiVersion: chaos-mesh.org/v1alpha1
kind: Workflow
spec:
  templates:
    - name: wait-a
      templateType: Suspend
      deadline: 250ms
    - name: kill-a
      templateType: PodChaos
      podChaos:
        action: pod-kill
        selector:
          pods:
            default:
              - pod-a
    - name: serial-a
      templateType: Serial
      children:
        - wait-a
        - kill-a
"""

    schedule = _extract_fault_schedule(wf_yaml)

    assert len(schedule) == 1
    assert schedule[0]["pod"] == "pod-a"
    assert schedule[0]["delay"] == "250ms"
    assert schedule[0]["delay_ms"] == 250.0
