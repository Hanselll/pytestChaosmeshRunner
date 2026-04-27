from __future__ import annotations

from typing import Any

from .inventory import resolve_network_element_spec


CONFIG_ACTIONS = {
    "config-read",
    "config-query",
    "config-write",
    "config-exec",
    "config-dnn-compare",
    "config-vrf-compare",
    "config-generic-compare",
}

PERF_ACTIONS = {
    "perf-metric-query",
    "perf-pagination-check",
    "perf-export-verify",
    "perf-db-compare",
}

TESTCASE_ACTIONS = {
    "config-write",
    "config-query",
    "config-dnn-compare",
    "config-vrf-compare",
    "config-generic-compare",
    "perf-metric-query",
    "perf-pagination-check",
    "perf-export-verify",
    "perf-db-compare",
}


def _ensure_dict(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} must be an object.")
    return value


def _ensure_list(value: Any, name: str) -> list:
    if not isinstance(value, list):
        raise RuntimeError(f"{name} must be a list.")
    return value


def _ensure_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{name} must be a non-empty string.")
    return value


def _ensure_string_list(value: Any, name: str) -> list[str]:
    items = _ensure_list(value, name)
    if not all(isinstance(item, str) and item.strip() for item in items):
        raise RuntimeError(f"{name} must contain non-empty strings.")
    return items


def _validate_db_config(db_config: Any, required: bool) -> None:
    if db_config is None:
        if required:
            raise RuntimeError("task.db must be provided.")
        return
    db = _ensure_dict(db_config, "task.db")
    for key in ("host", "port", "user", "password", "database"):
        if key not in db:
            raise RuntimeError(f"task.db.{key} is required.")


def _validate_compare_spec(task: dict) -> None:
    compare = _ensure_dict(task.get("compare"), "task.compare")
    field_map = _ensure_dict(compare.get("web_to_db_field_map"), "task.compare.web_to_db_field_map")
    if not field_map:
        raise RuntimeError("task.compare.web_to_db_field_map must not be empty.")
    for web_field, db_field in field_map.items():
        _ensure_string(web_field, "task.compare.web_to_db_field_map key")
        _ensure_string(db_field, f"task.compare.web_to_db_field_map[{web_field!r}]")
    if "compare_fields" in compare:
        _ensure_string_list(compare["compare_fields"], "task.compare.compare_fields")
    if "key_fields" in compare:
        _ensure_string_list(compare["key_fields"], "task.compare.key_fields")


def _merge_default_task(defaults: dict, task: dict) -> dict:
    merged = dict(defaults)
    merged.update(task)
    for key in ("params", "db", "compare"):
        if key in defaults or key in task:
            merged_value = dict(defaults.get(key, {}))
            merged_value.update(task.get(key, {}))
            merged[key] = merged_value
    return merged


def validate_config_task(command: str, task: Any) -> dict:
    normalized = _ensure_dict(task, "task")
    if command not in CONFIG_ACTIONS:
        raise RuntimeError(f"Unsupported config command: {command}")

    resolve_network_element_spec(normalized)
    if command in {"config-read", "config-query", "config-write", "config-exec", "config-generic-compare"}:
        _ensure_string_list(normalized.get("path_by_name"), "task.path_by_name")

    if "params" in normalized:
        _ensure_dict(normalized["params"], "task.params")

    if command in {"config-dnn-compare", "config-vrf-compare", "config-generic-compare"}:
        _validate_db_config(normalized.get("db"), required=True)
    elif "db" in normalized:
        _validate_db_config(normalized.get("db"), required=False)

    if command == "config-dnn-compare" and "dnn_name" in normalized:
        _ensure_string(normalized["dnn_name"], "task.dnn_name")

    if command == "config-vrf-compare" and "vrf_id" in normalized:
        value = normalized["vrf_id"]
        if not isinstance(value, (str, int)):
            raise RuntimeError("task.vrf_id must be a string or integer.")

    if command == "config-generic-compare":
        db = _ensure_dict(normalized.get("db"), "task.db")
        _ensure_string(db.get("array_key"), "task.db.array_key")
        _validate_compare_spec(normalized)

    return normalized


def validate_perf_task(command: str, task: Any) -> dict:
    normalized = _ensure_dict(task, "task")
    if command not in PERF_ACTIONS:
        raise RuntimeError(f"Unsupported perf command: {command}")

    if "target" in normalized:
        _ensure_string(normalized["target"], "task.target")
    if "filters" in normalized:
        _ensure_dict(normalized["filters"], "task.filters")
    if "compare_fields" in normalized:
        _ensure_string_list(normalized["compare_fields"], "task.compare_fields")

    if command == "perf-db-compare":
        _validate_db_config(normalized.get("db"), required=True)
        _ensure_string(normalized.get("source", "pm-metrics-metadata"), "task.source")
        if normalized.get("source") == "timeseries-non-null":
            _ensure_string(normalized.get("table"), "task.table")
            if "metric_codes" in normalized:
                metric_codes = normalized["metric_codes"]
                if not isinstance(metric_codes, str):
                    _ensure_list(metric_codes, "task.metric_codes")

    return normalized


def validate_testcase(case_data: Any) -> dict:
    case = _ensure_dict(case_data, "testcase")
    _ensure_string(case.get("name"), "testcase.name")

    defaults = case.get("defaults", {})
    defaults = _ensure_dict(defaults, "testcase.defaults")
    variables = case.get("variables", {})
    if variables:
        _ensure_dict(variables, "testcase.variables")

    for phase_name in ("setup", "steps", "teardown"):
        phase = _ensure_list(case.get(phase_name, []), f"testcase.{phase_name}")
        for index, step in enumerate(phase, start=1):
            step_obj = _ensure_dict(step, f"testcase.{phase_name}[{index}]")
            action = _ensure_string(step_obj.get("action"), f"testcase.{phase_name}[{index}].action")
            if action not in TESTCASE_ACTIONS:
                raise RuntimeError(f"Unsupported testcase action: {action}")
            task = _ensure_dict(step_obj.get("task"), f"testcase.{phase_name}[{index}].task")
            merged_task = _merge_default_task(defaults, task)
            if action in CONFIG_ACTIONS:
                validate_config_task(action, merged_task)
            else:
                validate_perf_task(action, merged_task)
            assertions = step_obj.get("assertions", [])
            _ensure_list(assertions, f"testcase.{phase_name}[{index}].assertions")
            retry = step_obj.get("retry")
            if retry is not None:
                retry_obj = _ensure_dict(retry, f"testcase.{phase_name}[{index}].retry")
                if "attempts" in retry_obj and not isinstance(retry_obj["attempts"], int):
                    raise RuntimeError(f"testcase.{phase_name}[{index}].retry.attempts must be an integer.")
                if "interval_seconds" in retry_obj and not isinstance(retry_obj["interval_seconds"], (int, float)):
                    raise RuntimeError(f"testcase.{phase_name}[{index}].retry.interval_seconds must be numeric.")

    return case
