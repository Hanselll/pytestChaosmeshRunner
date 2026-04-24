# -*- coding: utf-8 -*-
import pytest

from chaos_runner.case_schema import CaseValidationError, validate_case


def test_validate_case_accepts_by_label_values_and_sdb_pods():
    case = {
        "name": "schema-new-finders",
        "renderer": "modular_chaos",
        "targets": [
            {
                "id": "upu_main_pool",
                "finder": "by_label_values",
                "label_key": "app.kubernetes.io/component",
                "label_values": ["dupf-pod-upu-1", "dupf-pod-upu-2"],
            },
            {
                "id": "sdb_component",
                "finder": "sdb_pods",
            },
        ],
        "faults": [
            {
                "type": "network_delay",
                "selectors": {
                    "from": "sdb_component",
                    "to": "upu_main_pool",
                },
                "direction": "both",
                "duration": "30s",
                "delay": {
                    "latency": "300ms",
                    "jitter": "1000ms",
                },
            }
        ],
    }

    assert validate_case(case) is case


def test_validate_case_rejects_empty_label_values():
    case = {
        "name": "schema-new-finders-invalid",
        "renderer": "modular_chaos",
        "targets": [
            {
                "id": "upu_main_pool",
                "finder": "by_label_values",
                "label_key": "app.kubernetes.io/component",
                "label_values": [],
            }
        ],
        "faults": [
            {
                "type": "pod_kill",
                "target": "upu_main_pool",
            }
        ],
    }

    with pytest.raises(CaseValidationError, match="label_values"):
        validate_case(case)
