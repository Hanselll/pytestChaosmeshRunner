from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable

from .config_ops import (
    compare_dnn_web_with_db,
    compare_generic_config_web_with_db,
    compare_vrf_web_with_db,
    execute_config_task,
    query_config_task,
)
from .perf_ops import (
    check_metric_management_pagination,
    compare_perf_with_db,
    query_metric_management,
    verify_metric_export,
)
from .schemas import validate_config_task, validate_perf_task, validate_testcase


ActionHandler = Callable[[Any, dict, Path], dict]

ACTION_HANDLERS: dict[str, ActionHandler] = {
    "config-write": execute_config_task,
    "config-query": query_config_task,
    "config-dnn-compare": compare_dnn_web_with_db,
    "config-generic-compare": compare_generic_config_web_with_db,
    "config-vrf-compare": compare_vrf_web_with_db,
    "perf-metric-query": query_metric_management,
    "perf-pagination-check": check_metric_management_pagination,
    "perf-export-verify": verify_metric_export,
    "perf-db-compare": compare_perf_with_db,
}

_TEMPLATE_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _get_by_path(data: Any, path: str, default: Any = None) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        if isinstance(current, list):
            try:
                index = int(part)
            except ValueError:
                return default
            if 0 <= index < len(current):
                current = current[index]
                continue
        return default
    return current


def _resolve_templates(value: Any, context: dict) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_templates(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_templates(item, context) for item in value]
    if not isinstance(value, str):
        return value

    matches = list(_TEMPLATE_PATTERN.finditer(value))
    if not matches:
        return value

    if len(matches) == 1 and matches[0].span() == (0, len(value)):
        resolved = _get_by_path(context, matches[0].group(1), value)
        return resolved

    rendered = value
    for match in matches:
        resolved = _get_by_path(context, match.group(1), match.group(0))
        rendered = rendered.replace(match.group(0), str(resolved))
    return rendered


def _merge_task_defaults(defaults: dict, task: dict) -> dict:
    merged = dict(defaults)
    merged.update(task)
    if "params" in defaults or "params" in task:
        params = dict(defaults.get("params", {}))
        params.update(task.get("params", {}))
        merged["params"] = params
    if "db" in defaults or "db" in task:
        db = dict(defaults.get("db", {}))
        db.update(task.get("db", {}))
        merged["db"] = db
    return merged


def _evaluate_assertions(assertions: list[dict], result: dict) -> list[dict]:
    evaluated = []
    for index, assertion in enumerate(assertions, start=1):
        path = assertion["path"]
        actual = _get_by_path(result, path)
        status = True
        detail = ""

        if "equals" in assertion:
            expected = assertion["equals"]
            status = actual == expected
            detail = f"expected {expected!r}, got {actual!r}"
        elif assertion.get("truthy"):
            status = bool(actual)
            detail = f"expected truthy, got {actual!r}"
        elif assertion.get("falsy"):
            status = not bool(actual)
            detail = f"expected falsy, got {actual!r}"
        elif "contains" in assertion:
            expected = assertion["contains"]
            status = expected in (actual or [])
            detail = f"expected {expected!r} in {actual!r}"
        elif "gte" in assertion:
            expected = assertion["gte"]
            status = actual is not None and actual >= expected
            detail = f"expected >= {expected!r}, got {actual!r}"
        elif "lte" in assertion:
            expected = assertion["lte"]
            status = actual is not None and actual <= expected
            detail = f"expected <= {expected!r}, got {actual!r}"
        else:
            raise RuntimeError(f"Unsupported assertion in step: {assertion}")

        evaluated.append(
            {
                "index": index,
                "path": path,
                "status": "passed" if status else "failed",
                "actual": actual,
                "assertion": assertion,
                "detail": detail,
            }
        )
    return evaluated


def _run_step_once(action: str, task: dict, step: dict, step_debug_dir: Path) -> dict:
    if action.startswith("config-"):
        validated_task = validate_config_task(action, task)
    else:
        validated_task = validate_perf_task(action, task)
    output = ACTION_HANDLERS[action](page=step["_page"], task=validated_task, debug_dir=step_debug_dir)
    assertions = _evaluate_assertions(step.get("assertions", []), output)
    failed_assertions = [item for item in assertions if item["status"] != "passed"]
    return {
        "status": "passed" if not failed_assertions else "failed",
        "task": validated_task,
        "output": output,
        "assertions": assertions,
        "failed_assertions": failed_assertions,
    }


def _compact_text(value: Any, limit: int = 120) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _step_summary(action: str, task: dict, result: dict) -> str:
    output = result.get("output", {}) if isinstance(result, dict) else {}
    if action == "config-dnn-compare":
        summary = output.get("summary", {})
        dnn_name = output.get("dnn_name") or task.get("dnn_name") or ""
        comparisons = output.get("comparisons", [])
        status = "match"
        if comparisons:
            status = comparisons[0].get("status", status)
        return (
            f"dnn={dnn_name} web={summary.get('web_row_count', 0)} "
            f"db={summary.get('db_row_count', 0)} status={status}"
        )

    if action == "config-vrf-compare":
        summary = output.get("summary", {})
        vrf_id = output.get("vrf_id") or task.get("vrf_id") or ""
        comparisons = output.get("comparisons", [])
        status = "match"
        if comparisons:
            status = comparisons[0].get("status", status)
        return (
            f"vrf_id={vrf_id} web={summary.get('web_row_count', 0)} "
            f"db={summary.get('db_row_count', 0)} status={status}"
        )

    if action == "config-generic-compare":
        summary = output.get("summary", {})
        comparisons = output.get("comparisons", [])
        status = "match"
        key_text = ""
        if comparisons:
            status = comparisons[0].get("status", status)
            row_key = comparisons[0].get("row_key", {})
            if row_key:
                key_text = " ".join(f"{k}={v}" for k, v in row_key.items())
        return (
            f"{key_text} web={summary.get('web_row_count', 0)} "
            f"db={summary.get('db_row_count', 0)} status={status}"
        ).strip()

    if action == "config-write":
        params = task.get("params", {}) if isinstance(task, dict) else {}
        key_values = []
        for key in ("DNN名称", "N3 VRFID", "N6 VRFID", "N9 VRFID", "S1U VRFID", "VRF ID"):
            if key in params:
                key_values.append(f"{key}={params[key]}")
        submitted = output.get("submitted")
        prefix = f"submitted={submitted}"
        if key_values:
            return f"{prefix} " + " ".join(key_values)
        return prefix

    if action == "config-query":
        headers = output.get("headers", [])
        rows = output.get("rows", [])
        return f"headers={len(headers)} rows={len(rows)}"

    if action == "perf-metric-query":
        filters = output.get("filters", {})
        filter_text = " ".join(f"{k}={v}" for k, v in filters.items())
        return f"rows={output.get('row_count', 0)} metrics={len(output.get('metric_codes', []))} {filter_text}".strip()

    if action == "perf-pagination-check":
        pagination = output.get("pagination", {})
        return (
            f"pages={pagination.get('page_count', 1)} total={pagination.get('total', 0)} "
            f"next_ok={output.get('next_ok')} prev_ok={output.get('previous_ok')}"
        )

    if action == "perf-export-verify":
        summary = output.get("summary", {})
        return (
            f"web={output.get('web_row_count', 0)} export={output.get('export_row_count', 0)} "
            f"match={summary.get('all_match', False)}"
        )

    if action == "perf-db-compare":
        source = output.get("source", "")
        summary = output.get("summary", {})
        if source == "pm-metrics-metadata":
            return (
                f"metrics={len(output.get('metric_codes', []))} "
                f"mismatch={summary.get('mismatch_row_count', 0)} "
                f"match={summary.get('all_match', False)}"
            )
        if source == "timeseries-non-null":
            return (
                f"table={output.get('table', '')} points={output.get('point_count', 0)} "
                f"data={summary.get('all_metrics_have_data', False)}"
            )

    return f"status={result.get('status', 'unknown')}"


def _run_phase(
    page,
    phase_name: str,
    steps: list[dict],
    context: dict,
    debug_root: Path,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[dict], bool]:
    phase_results = []
    phase_ok = True

    for index, raw_step in enumerate(steps, start=1):
        step = _resolve_templates(raw_step, context)
        step_id = step.get("id") or f"{phase_name}_{index:02d}"
        action = step["action"]
        if action not in ACTION_HANDLERS:
            raise RuntimeError(f"Unsupported testcase action: {action}")

        task_defaults = _resolve_templates(context.get("defaults", {}), context)
        task = _merge_task_defaults(task_defaults, step.get("task", {}))
        step_debug_dir = debug_root / phase_name / step_id

        step_result = {
            "id": step_id,
            "name": step.get("name", step_id),
            "phase": phase_name,
            "action": action,
            "debug_dir": str(step_debug_dir),
            "continue_on_failure": bool(step.get("continue_on_failure", False)),
        }
        started_at = time.time()
        retry = step.get("retry", {})
        attempts = max(1, int(retry.get("attempts", 1)))
        interval_seconds = float(retry.get("interval_seconds", 0))
        if progress:
            progress(f"[{phase_name}] start {step_id} ({action})")

        attempt_records = []
        for attempt in range(1, attempts + 1):
            attempt_debug_dir = step_debug_dir / f"attempt_{attempt:02d}" if attempts > 1 else step_debug_dir
            step_with_page = dict(step)
            step_with_page["_page"] = page
            try:
                attempt_result = _run_step_once(action, task, step_with_page, attempt_debug_dir)
                attempt_records.append(
                    {
                        "attempt": attempt,
                        "status": attempt_result["status"],
                        "assertions": attempt_result["assertions"],
                    }
                )
                if attempt_result["status"] == "passed":
                    step_result.update(
                        {
                            "status": "passed",
                            "task": task,
                            "output": attempt_result["output"],
                            "assertions": attempt_result["assertions"],
                            "attempts_used": attempt,
                            "attempt_history": attempt_records,
                        }
                    )
                    step_result["summary_text"] = _step_summary(action, task, step_result)
                    break
                if progress:
                    progress(
                        f"[{phase_name}] retry {step_id} attempt={attempt}/{attempts} "
                        f"failed_assertions={len(attempt_result['failed_assertions'])}"
                    )
                if attempt < attempts and interval_seconds > 0:
                    time.sleep(interval_seconds)
                if attempt == attempts:
                    step_result.update(
                        {
                            "status": "failed",
                            "task": task,
                            "output": attempt_result["output"],
                            "assertions": attempt_result["assertions"],
                            "attempts_used": attempt,
                            "attempt_history": attempt_records,
                        }
                    )
                    step_result["summary_text"] = _step_summary(action, task, step_result)
                    phase_ok = False
                    if progress:
                        progress(
                            f"[{phase_name}] fail {step_id} "
                            f"{step_result['summary_text']} assertions={len(attempt_result['failed_assertions'])}"
                        )
            except Exception as exc:
                attempt_records.append(
                    {
                        "attempt": attempt,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                if progress:
                    progress(f"[{phase_name}] retry {step_id} attempt={attempt}/{attempts} error={exc}")
                if attempt < attempts and interval_seconds > 0:
                    time.sleep(interval_seconds)
                if attempt == attempts:
                    step_result.update(
                        {
                            "status": "failed",
                            "task": task,
                            "error": str(exc),
                            "assertions": [],
                            "attempts_used": attempt,
                            "attempt_history": attempt_records,
                        }
                    )
                    phase_ok = False
                    if progress:
                        progress(f"[{phase_name}] fail {step_id} error={exc}")

        context["steps"][step_id] = step_result
        step_result["duration_seconds"] = round(time.time() - started_at, 3)
        phase_results.append(step_result)
        if progress and step_result["status"] == "passed":
            summary_text = step_result.get("summary_text", "")
            suffix = f" {summary_text}" if summary_text else ""
            progress(f"[{phase_name}] done {step_id}{suffix} duration={step_result['duration_seconds']}s")

        if step_result["status"] != "passed" and not step_result["continue_on_failure"] and phase_name != "teardown":
            break

    return phase_results, phase_ok


def run_testcase(page, case_data: dict, debug_root: Path, progress: Callable[[str], None] | None = None) -> dict:
    case_data = validate_testcase(case_data)
    context = {
        "case": case_data,
        "defaults": case_data.get("defaults", {}),
        "variables": case_data.get("variables", {}),
        "steps": {},
    }

    if progress:
        progress(f"[testcase] start {case_data.get('name', '')}")
    setup_results, setup_ok = _run_phase(page, "setup", case_data.get("setup", []), context, debug_root, progress)
    step_results, steps_ok = _run_phase(page, "steps", case_data.get("steps", []), context, debug_root, progress)
    teardown_results, teardown_ok = _run_phase(page, "teardown", case_data.get("teardown", []), context, debug_root, progress)

    all_results = setup_results + step_results + teardown_results
    failed_steps = [item["id"] for item in all_results if item["status"] != "passed"]
    result = {
        "mode": "testcase",
        "name": case_data.get("name", ""),
        "description": case_data.get("description", ""),
        "status": "passed" if setup_ok and steps_ok and teardown_ok else "failed",
        "variables": context["variables"],
        "summary": {
            "setup_ok": setup_ok,
            "steps_ok": steps_ok,
            "teardown_ok": teardown_ok,
            "total_steps": len(all_results),
            "failed_steps": failed_steps,
        },
        "phases": {
            "setup": setup_results,
            "steps": step_results,
            "teardown": teardown_results,
        },
    }
    if progress:
        progress(f"[testcase] done status={result['status']}")
    return result
