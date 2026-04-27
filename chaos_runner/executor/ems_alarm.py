# -*- coding: utf-8 -*-
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from chaos_runner import config

_DATETIME_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\b")


def _safe_name(value):
    text = str(value or "case").strip()
    out = []
    for ch in text:
        out.append(ch if ch.isalnum() or ch in ("-", "_", ".") else "-")
    return ("".join(out).strip("-") or "case")[:80]


def _default_output_dir(log_dir, wf_name, case_ts):
    base = str(log_dir or "").strip()
    if not base:
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "artifacts", "logs")
    return os.path.join(base, "ems_alarm_{}_{}".format(_safe_name(wf_name), _safe_name(case_ts)))


def _project_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _resolve_project_path(path):
    text = str(path or "").strip()
    if not text:
        return ""
    if os.path.isabs(text):
        return os.path.abspath(text)
    return os.path.abspath(os.path.join(_project_root(), text))


def _load_summary(output_dir):
    path = os.path.join(output_dir, "alarm_export_summary.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_dt(value):
    text = str(value or "").strip()
    match = _DATETIME_RE.search(text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(0).replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _row_datetimes(row):
    out = []
    for value in (row or {}).values():
        parsed = _parse_dt(value)
        if parsed is not None:
            out.append(parsed)
    return out


def _load_alarm_table(output_dir, name):
    path = os.path.join(output_dir, "{}.json".format(name))
    if not os.path.exists(path):
        return {"headers": [], "rows": []}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f) or {}
    return {
        "headers": list(data.get("headers") or []),
        "rows": list(data.get("rows") or []),
        "path": path,
    }


def _row_value(row, headers, index):
    if index >= len(headers):
        return ""
    return str((row or {}).get(headers[index]) or "").strip()


def _summarize_alarm_row(row, headers):
    times = _row_datetimes(row)
    event_time = max(times).strftime("%Y-%m-%d %H:%M:%S") if times else ""
    severity = _row_value(row, headers, 1)
    ne_type = _row_value(row, headers, 3)
    ne_name = _row_value(row, headers, 4)
    alarm_name = _row_value(row, headers, 5)
    location = _row_value(row, headers, 6)
    count = _row_value(row, headers, 7)
    first_time = _row_value(row, headers, 8)
    latest_time = _row_value(row, headers, 9)
    clear_time = _row_value(row, headers, 11)
    return {
        "event_time": event_time,
        "severity": severity,
        "ne_type": ne_type,
        "ne_name": ne_name,
        "alarm_name": alarm_name,
        "location": location,
        "count": count,
        "first_time": first_time,
        "latest_time": latest_time,
        "clear_time": clear_time,
    }


def _filter_alarm_rows(table, since_time):
    if since_time is None:
        return []
    rows = []
    for row in table.get("rows") or []:
        row_times = _row_datetimes(row)
        if any(item >= since_time for item in row_times):
            rows.append(_summarize_alarm_row(row, table.get("headers") or []))
    rows.sort(key=lambda item: item.get("event_time") or "")
    return rows


def _local_timezone():
    return datetime.now().astimezone().tzinfo


def _convert_since_time_to_ems_timezone(since_time):
    if since_time is None:
        return None
    ems_offset = int(getattr(config, "EMS_ALARM_TIMEZONE_OFFSET_HOURS", 0) or 0)
    ems_tz = timezone(timedelta(hours=ems_offset))
    if since_time.tzinfo is None:
        local_time = since_time.replace(tzinfo=_local_timezone())
    else:
        local_time = since_time
    return local_time.astimezone(ems_tz).replace(tzinfo=None)


def _recent_alarm_summary(output_dir, since_time):
    activity = _load_alarm_table(output_dir, "activity_alarm")
    history = _load_alarm_table(output_dir, "history_alarm")
    ems_since_time = _convert_since_time_to_ems_timezone(since_time)
    return {
        "since_time": ems_since_time.strftime("%Y-%m-%d %H:%M:%S") if ems_since_time else "",
        "since_time_ems": ems_since_time.strftime("%Y-%m-%d %H:%M:%S") if ems_since_time else "",
        "since_time_local": since_time.strftime("%Y-%m-%d %H:%M:%S") if since_time else "",
        "ems_timezone_offset_hours": int(getattr(config, "EMS_ALARM_TIMEZONE_OFFSET_HOURS", 0) or 0),
        "activity": _filter_alarm_rows(activity, ems_since_time),
        "history": _filter_alarm_rows(history, ems_since_time),
        "activity_path": activity.get("path", ""),
        "history_path": history.get("path", ""),
    }


def _counts_from_summary(summary):
    counts = {}
    for key, value in (summary or {}).items():
        if key == "overview":
            counts[key] = ((value or {}).get("summary") or {}).get("totalCount", 0)
        elif isinstance(value, dict):
            counts[key] = len(value.get("rows") or [])
    return counts


def run_ems_alarm_query(wf_name, case_ts, log_dir="", since_time=None):
    if not bool(getattr(config, "EMS_ALARM_ENABLED", False)):
        return {"enabled": False, "skipped": True, "reason": "EMS_ALARM_ENABLED is false"}

    target = str(getattr(config, "EMS_ALARM_TARGET", "all") or "all").strip().lower()
    if target not in ("overview", "activity", "history", "all"):
        raise RuntimeError("EMS_ALARM_TARGET must be one of overview/activity/history/all")

    ems_dir = _resolve_project_path(getattr(config, "EMS_AUTOMATION_DIR", ""))
    if not os.path.isdir(ems_dir):
        raise RuntimeError("EMS_AUTOMATION_DIR not found: {}".format(ems_dir))

    output_dir = str(getattr(config, "EMS_ALARM_OUTPUT_DIR", "") or "").strip()
    if not output_dir:
        output_dir = _default_output_dir(log_dir, wf_name, case_ts)
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    python_exe = str(getattr(config, "EMS_ALARM_PYTHON", "") or "").strip() or sys.executable
    cmd = [
        python_exe,
        "-m",
        "ems_automation.cli",
        "alarm-read",
        "--target",
        target,
        "--output-dir",
        output_dir,
    ]
    timeout = int(getattr(config, "EMS_ALARM_TIMEOUT_SECONDS", 180) or 180)

    env = os.environ.copy()
    old_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = ems_dir if not old_pythonpath else ems_dir + os.pathsep + old_pythonpath

    proc = subprocess.run(
        cmd,
        cwd=ems_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )

    summary = _load_summary(output_dir) if proc.returncode == 0 else {}
    result = {
        "enabled": True,
        "skipped": False,
        "target": target,
        "output_dir": output_dir,
        "summary_path": os.path.join(output_dir, "alarm_export_summary.json"),
        "counts": _counts_from_summary(summary),
        "rc": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
        "command": cmd,
    }
    result["recent_alarms"] = _recent_alarm_summary(output_dir, since_time)
    if proc.returncode != 0:
        raise RuntimeError("EMS alarm query failed rc={}: {}".format(proc.returncode, result["stderr"] or result["stdout"]))
    return result


def format_recent_alarm_log_lines(result):
    recent = (result or {}).get("recent_alarms") or {}
    since = recent.get("since_time_ems") or recent.get("since_time") or ""
    local_since = recent.get("since_time_local") or ""
    offset = recent.get("ems_timezone_offset_hours")
    tz_part = " ems_tz=UTC{:+d}".format(int(offset)) if offset is not None else ""
    lines = [
        "[EMS] alarms since case_start_ems={} case_start_local={}{} activity={} history={}".format(
            since,
            local_since,
            tz_part,
            len(recent.get("activity") or []),
            len(recent.get("history") or []),
        )
    ]
    for section in ("activity", "history"):
        rows = recent.get(section) or []
        if not rows:
            lines.append("[EMS] {} alarms since case start: none".format(section))
            continue
        lines.append("[EMS] {} alarms since case start:".format(section))
        for idx, row in enumerate(rows, 1):
            lines.append(
                "[EMS]   {idx}. time={time} severity={severity} ne={ne} alarm={alarm} location={location} count={count} first={first} latest={latest} clear={clear}".format(
                    idx=idx,
                    time=row.get("event_time", ""),
                    severity=row.get("severity", ""),
                    ne=row.get("ne_name", "") or row.get("ne_type", ""),
                    alarm=row.get("alarm_name", ""),
                    location=row.get("location", ""),
                    count=row.get("count", ""),
                    first=row.get("first_time", ""),
                    latest=row.get("latest_time", ""),
                    clear=row.get("clear_time", ""),
                )
            )
    return lines
