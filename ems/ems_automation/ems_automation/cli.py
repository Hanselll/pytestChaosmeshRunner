from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .alarm_ops import (
    collect_alarm_data,
    list_alarm_rules,
    write_alarm_rule,
)
from .auth import run_auth_check, run_manual_login
from .browser import open_page
from .config_ops import (
    compare_dnn_web_with_db,
    compare_generic_config_web_with_db,
    compare_vrf_web_with_db,
    execute_config_task,
    export_config_tree,
    inspect_config_task,
    query_config_task,
)
from .perf_ops import (
    check_metric_management_pagination,
    collect_perf_data,
    compare_perf_with_db,
    create_metric_task,
    create_monitor_task,
    create_query_task,
    query_metric_management,
    verify_metric_export,
)
from .pages.command_processing_page import CommandProcessingPage
from .schemas import validate_config_task, validate_perf_task, validate_testcase
from .settings import resolve_output_path
from .testcase_ops import run_testcase


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ems-cli",
        description="EMS automation toolkit for config and alarm operations.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_tree = sub.add_parser("config-tree", help="Export config tree and field metadata.")
    p_tree.add_argument("--ne", required=True, help="Network element IP.")
    p_tree.add_argument("--output", default="config_tree_with_fields.yaml")
    sub.add_parser("ne-list", help="List visible network element options from the command processing page.")

    p_auth = sub.add_parser("auth-login", help="Open EMS in a visible browser, let the operator complete login, then save storage state.")
    p_auth.add_argument("--output", default="", help="Optional storage_state.json path.")

    p_auth_check = sub.add_parser("auth-check", help="Verify the current EMS session in headless mode and refresh storage state.")
    p_auth_check.add_argument("--output", default="", help="Optional storage_state.json path.")

    p_read = sub.add_parser("config-read", help="Open a config path and read current form fields.")
    p_read.add_argument("--task", required=True, help="JSON task file.")
    p_read.add_argument("--debug-dir", default="debug_config_read")

    p_query = sub.add_parser("config-query", help="Execute a SHOW-like config command and extract result text.")
    p_query.add_argument("--task", required=True, help="JSON task file.")
    p_query.add_argument("--debug-dir", default="debug_config_query")

    p_write = sub.add_parser("config-write", help="Fill a config form and optionally submit.")
    p_write.add_argument("--task", required=True, help="JSON task file.")
    p_write.add_argument("--debug-dir", default="debug_config_write")

    p_exec = sub.add_parser("config-exec", help="Backward-compatible alias of config-write.")
    p_exec.add_argument("--task", required=True, help="JSON task file.")
    p_exec.add_argument("--debug-dir", default="debug_config_exec")

    p_dnn_compare = sub.add_parser("config-dnn-compare", help="Compare DNN web query rows with database DNN config.")
    p_dnn_compare.add_argument("--task", required=True, help="JSON task file.")
    p_dnn_compare.add_argument("--debug-dir", default="debug_config_dnn_compare")

    p_vrf_compare = sub.add_parser("config-vrf-compare", help="Compare VRF web query rows with database VRF config.")
    p_vrf_compare.add_argument("--task", required=True, help="JSON task file.")
    p_vrf_compare.add_argument("--debug-dir", default="debug_config_vrf_compare")

    p_generic_compare = sub.add_parser("config-generic-compare", help="Compare generic config web query rows with database snapshot rows.")
    p_generic_compare.add_argument("--task", required=True, help="JSON task file.")
    p_generic_compare.add_argument("--debug-dir", default="debug_config_generic_compare")

    p_alarm = sub.add_parser("alarm-read", help="Read alarm overview/table data.")
    p_alarm.add_argument(
        "--target",
        choices=["overview", "activity", "history", "all"],
        default="all",
    )
    p_alarm.add_argument("--output-dir", default="alarm_exports")

    p_rule_read = sub.add_parser("alarm-rule-read", help="Read alarm rule list/table data.")
    p_rule_read.add_argument("--rule-page", required=True, help="Alarm rule tab name.")
    p_rule_read.add_argument("--output", default="")

    p_rule_write = sub.add_parser("alarm-rule-write", help="Create an alarm rule and optionally submit.")
    p_rule_write.add_argument("--task", required=True, help="JSON task file.")
    p_rule_write.add_argument("--debug-dir", default="alarm_rule_debug")

    p_perf_read = sub.add_parser("perf-read", help="Read performance page tables and visible form controls.")
    p_perf_read.add_argument(
        "--target",
        choices=["metric-management", "task", "monitor", "query", "statistics", "all"],
        default="all",
    )
    p_perf_read.add_argument("--output-dir", default="perf_exports")

    p_metric_create = sub.add_parser("perf-metric-create", help="Fill the custom metric creation page.")
    p_metric_create.add_argument("--task", required=True, help="JSON task file.")
    p_metric_create.add_argument("--debug-dir", default="debug_perf_metric_create")

    p_metric_query = sub.add_parser("perf-metric-query", help="Query metric management with filters and extract rows.")
    p_metric_query.add_argument("--task", required=True, help="JSON task file.")
    p_metric_query.add_argument("--debug-dir", default="debug_perf_metric_query")

    p_metric_page = sub.add_parser("perf-pagination-check", help="Verify metric management pagination behaviour.")
    p_metric_page.add_argument("--task", required=True, help="JSON task file.")
    p_metric_page.add_argument("--debug-dir", default="debug_perf_metric_pagination")

    p_metric_export = sub.add_parser("perf-export-verify", help="Verify metric export matches the web query result.")
    p_metric_export.add_argument("--task", required=True, help="JSON task file.")
    p_metric_export.add_argument("--debug-dir", default="debug_perf_metric_export")

    p_perf_db = sub.add_parser("perf-db-compare", help="Compare performance metric metadata or timeseries data with DB.")
    p_perf_db.add_argument("--task", required=True, help="JSON task file.")
    p_perf_db.add_argument("--debug-dir", default="debug_perf_db_compare")

    p_monitor_create = sub.add_parser("perf-monitor-create", help="Fill the performance monitor creation wizard.")
    p_monitor_create.add_argument("--task", required=True, help="JSON task file.")
    p_monitor_create.add_argument("--debug-dir", default="debug_perf_monitor_create")

    p_query_create = sub.add_parser("perf-query-create", help="Fill the performance query creation wizard.")
    p_query_create.add_argument("--task", required=True, help="JSON task file.")
    p_query_create.add_argument("--debug-dir", default="debug_perf_query_create")

    p_testcase = sub.add_parser("testcase-run", help="Run a structured EMS testcase with phased steps and assertions.")
    p_testcase.add_argument("--task", required=True, help="JSON testcase file.")
    p_testcase.add_argument("--debug-dir", default="debug_testcase")

    return parser.parse_args()


def _load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _ensure_dir(path: str) -> Path:
    out = resolve_output_path(path, "runs")
    out.mkdir(parents=True, exist_ok=True)
    return out


def _emit_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _print_testcase_compact(result: dict, output_path: Path) -> None:
    print(f"testcase: {result.get('name', '')}")
    print(f"status: {result.get('status', '')}")
    summary = result.get("summary", {})
    if summary.get("failed_steps"):
        print("failed_steps: " + ", ".join(summary["failed_steps"]))
    for phase_name in ("setup", "steps", "teardown"):
        phase_items = result.get("phases", {}).get(phase_name, [])
        for item in phase_items:
            line = f"[{phase_name}] {item.get('id', '')} {item.get('status', '')}"
            summary_text = item.get("summary_text")
            if summary_text:
                line += f" {summary_text}"
            if item.get("status") != "passed" and item.get("assertions"):
                failed = [a for a in item["assertions"] if a.get("status") != "passed"]
                if failed:
                    line += " failed=" + "; ".join(
                        f"{a.get('path')} ({a.get('detail')})" for a in failed
                    )
            print(line)
    print(f"full_result: {output_path}")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = parse_args()

    if args.cmd == "config-tree":
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with open_page() as page:
            result = export_config_tree(page, args.ne, output)
        print(json.dumps({"output": str(output), "tree_nodes": len(result["tree"])}, ensure_ascii=False, indent=2))
        return

    if args.cmd == "ne-list":
        with open_page() as page:
            result = CommandProcessingPage(page).list_network_elements()
        print(json.dumps({"count": len(result), "items": result}, ensure_ascii=False, indent=2))
        return

    if args.cmd == "auth-login":
        output = resolve_output_path(args.output, "auth") if args.output else None
        result = run_manual_login(output_path=output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cmd == "auth-check":
        output = resolve_output_path(args.output, "auth") if args.output else None
        result = run_auth_check(output_path=output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cmd == "config-read":
        task = validate_config_task("config-read", _load_json(args.task))
        debug_dir = _ensure_dir(args.debug_dir)
        with open_page() as page:
            result = inspect_config_task(page, task, debug_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cmd == "config-query":
        task = validate_config_task("config-query", _load_json(args.task))
        debug_dir = _ensure_dir(args.debug_dir)
        with open_page() as page:
            result = query_config_task(page, task, debug_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cmd in {"config-write", "config-exec"}:
        task = validate_config_task(args.cmd, _load_json(args.task))
        debug_dir = _ensure_dir(args.debug_dir)
        with open_page() as page:
            result = execute_config_task(page, task, debug_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cmd == "config-dnn-compare":
        task = validate_config_task("config-dnn-compare", _load_json(args.task))
        debug_dir = _ensure_dir(args.debug_dir)
        with open_page() as page:
            result = compare_dnn_web_with_db(page, task, debug_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cmd == "config-vrf-compare":
        task = validate_config_task("config-vrf-compare", _load_json(args.task))
        debug_dir = _ensure_dir(args.debug_dir)
        with open_page() as page:
            result = compare_vrf_web_with_db(page, task, debug_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cmd == "config-generic-compare":
        task = validate_config_task("config-generic-compare", _load_json(args.task))
        debug_dir = _ensure_dir(args.debug_dir)
        with open_page() as page:
            result = compare_generic_config_web_with_db(page, task, debug_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cmd == "alarm-read":
        output_dir = _ensure_dir(args.output_dir)
        with open_page() as page:
            outputs = collect_alarm_data(page, args.target, output_dir)
        summary = output_dir / "alarm_export_summary.json"
        summary.write_text(json.dumps(outputs, ensure_ascii=False, indent=2), encoding="utf-8")
        counts = {}
        for key, value in outputs.items():
            if key == "overview":
                counts[key] = value.get("summary", {}).get("totalCount", 0)
            else:
                counts[key] = len(value.get("rows", []))
        print(json.dumps({"output": str(summary), "targets": list(outputs.keys()), "counts": counts}, ensure_ascii=False, indent=2))
        return

    if args.cmd == "alarm-rule-read":
        output = Path(args.output) if args.output else None
        with open_page() as page:
            result = list_alarm_rules(page, args.rule_page, output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cmd == "alarm-rule-write":
        task = _load_json(args.task)
        debug_dir = _ensure_dir(args.debug_dir)
        with open_page() as page:
            result = write_alarm_rule(page, task, debug_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cmd == "perf-read":
        output_dir = _ensure_dir(args.output_dir)
        with open_page() as page:
            outputs = collect_perf_data(page, args.target, output_dir)
        counts = {
            key: len(value.get("table_row_dicts_all_pages", value.get("table_rows", [])))
            for key, value in outputs.items()
        }
        print(
            json.dumps(
                {"output": str(output_dir / "perf_export_summary.json"), "targets": list(outputs.keys()), "counts": counts},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.cmd == "perf-metric-create":
        task = _load_json(args.task)
        debug_dir = _ensure_dir(args.debug_dir)
        with open_page() as page:
            result = create_metric_task(page, task, debug_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cmd == "perf-metric-query":
        task = validate_perf_task("perf-metric-query", _load_json(args.task))
        debug_dir = _ensure_dir(args.debug_dir)
        with open_page() as page:
            result = query_metric_management(page, task, debug_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cmd == "perf-pagination-check":
        task = validate_perf_task("perf-pagination-check", _load_json(args.task))
        debug_dir = _ensure_dir(args.debug_dir)
        with open_page() as page:
            result = check_metric_management_pagination(page, task, debug_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cmd == "perf-export-verify":
        task = validate_perf_task("perf-export-verify", _load_json(args.task))
        debug_dir = _ensure_dir(args.debug_dir)
        with open_page() as page:
            result = verify_metric_export(page, task, debug_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cmd == "perf-db-compare":
        task = validate_perf_task("perf-db-compare", _load_json(args.task))
        debug_dir = _ensure_dir(args.debug_dir)
        with open_page() as page:
            result = compare_perf_with_db(page, task, debug_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cmd == "perf-monitor-create":
        task = _load_json(args.task)
        debug_dir = _ensure_dir(args.debug_dir)
        with open_page() as page:
            result = create_monitor_task(page, task, debug_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cmd == "perf-query-create":
        task = _load_json(args.task)
        debug_dir = _ensure_dir(args.debug_dir)
        with open_page() as page:
            result = create_query_task(page, task, debug_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cmd == "testcase-run":
        task = validate_testcase(_load_json(args.task))
        debug_dir = _ensure_dir(args.debug_dir)
        with open_page() as page:
            result = run_testcase(page, task, debug_dir, progress=_emit_progress)
        result_path = debug_dir / "testcase_result.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        _print_testcase_compact(result, result_path)
        return


if __name__ == "__main__":
    main()
