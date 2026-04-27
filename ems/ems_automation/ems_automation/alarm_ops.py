from __future__ import annotations

import csv
import json
from pathlib import Path

from .ui import click_popup_item, click_text, click_top_menu, dump_debug, fill_form_item, open_home, wait


MENU_ALARM = "\u544a\u8b66"
MENU_OVERVIEW = "\u544a\u8b66\u7edf\u8ba1"
MENU_ACTIVITY = "\u6d3b\u52a8\u544a\u8b66"
MENU_HISTORY = "\u5386\u53f2\u544a\u8b66"
MENU_RULE = "\u544a\u8b66\u89c4\u5219"
BUTTON_ADD = "\u65b0\u589e"
BUTTON_CONFIRM = "\u786e\u5b9a"


def open_alarm_page(page, menu_name: str) -> None:
    last_error = None
    for attempt in range(2):
        try:
            open_home(page)
            click_top_menu(page, MENU_ALARM)
            click_popup_item(page, menu_name)
            wait(page, 3000)
            return
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                dump_debug(page, Path("debug_alarm_open") / f"attempt_{attempt + 1}")
                continue
            raise last_error


def normalize_overview_api(responses: dict) -> dict:
    data = {
        "menu": MENU_OVERVIEW,
        "summary": {},
        "by_ne_type": [],
        "by_alarm_type": [],
        "by_ne_group": [],
        "by_ne_top": [],
        "by_date": [],
        "raw": responses,
    }
    for url, item in responses.items():
        body = item.get("body", "")
        if not body:
            continue
        parsed = json.loads(body)
        if "/statistics?scence=ACTIVE" in url:
            data["summary"] = parsed
        elif url.endswith("/statisticsByNeType"):
            data["by_ne_type"] = parsed
        elif url.endswith("/statisticsByAlarmType"):
            data["by_alarm_type"] = parsed
        elif url.endswith("/statisticsByNeGroup"):
            data["by_ne_group"] = parsed.get("countByNeGroup", [])
            data["by_ne_top"] = parsed.get("countByNeName", [])
        elif url.endswith("/statisticsByDate"):
            data["by_date"] = parsed
    return data


def collect_overview(page, output_dir: Path | None = None) -> dict:
    responses = {}

    def on_response(resp):
        url = resp.url
        if "/api/web/fm/alarm/" not in url:
            return
        try:
            body = resp.text()
        except Exception as exc:
            body = f"<read error: {exc}>"
        responses[url] = {"status": resp.status, "headers": resp.headers, "body": body}

    page.on("response", on_response)
    open_alarm_page(page, MENU_OVERVIEW)
    wait(page, 5000)
    result = normalize_overview_api(responses)
    result["url"] = page.url
    if output_dir is not None:
        (output_dir / "alarm_overview.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return result


def extract_table_rows(page, expected_headers: list[str]) -> dict:
    js = """
    ({ expectedHeaders }) => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }
      function text(el) {
        return (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
      }
      const wrappers = Array.from(document.querySelectorAll('.el-table')).filter(visible);
      let target = null;
      for (const table of wrappers) {
        const headers = Array.from(table.querySelectorAll('.el-table__header-wrapper th .cell')).map(text).filter(Boolean);
        if (expectedHeaders.every(header => headers.includes(header))) {
          target = table;
          break;
        }
      }
      if (!target) return { headers: [], rows: [] };
      const rawHeaders = Array.from(target.querySelectorAll('.el-table__header-wrapper th')).map(th => {
        const cls = th.className || '';
        const label = text(th.querySelector('.cell') || th);
        return { cls, label };
      });
      const keepIndexes = [];
      const headers = [];
      rawHeaders.forEach((h, idx) => {
        if (!h.label) return;
        if (h.cls.includes('expand-column')) return;
        if (h.cls.includes('selection')) return;
        headers.push(h.label);
        keepIndexes.push(idx);
      });
      const rows = [];
      const trs = Array.from(target.querySelectorAll('.el-table__body-wrapper tbody tr.el-table__row'));
      for (const tr of trs) {
        const allTds = Array.from(tr.querySelectorAll('td'));
        if (!allTds.length) continue;
        const row = {};
        let hasValue = false;
        keepIndexes.forEach((tdIndex, headerIndex) => {
          const td = allTds[tdIndex];
          const value = td ? text(td.querySelector('.cell') || td) : '';
          row[headers[headerIndex]] = value;
          if (value) hasValue = true;
        });
        if (hasValue) rows.push(row);
      }
      return { headers, rows };
    }
    """
    return page.evaluate(js, {"expectedHeaders": expected_headers})


def write_csv(path: Path, rows: list[dict]) -> None:
    headers = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def collect_table_page(
    page,
    menu_name: str,
    route_name: str,
    output_dir: Path | None = None,
    expected_headers: list[str] | None = None,
) -> dict:
    open_alarm_page(page, menu_name)
    result = extract_table_rows(page, expected_headers or ["告警名称", "网元名称"])
    result["menu"] = menu_name
    result["url"] = page.url
    if output_dir is not None:
        (output_dir / f"{route_name}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_csv(output_dir / f"{route_name}.csv", result["rows"])
    return result


def collect_alarm_data(page, target: str, output_dir: Path) -> dict:
    outputs = {}
    if target in ("overview", "all"):
        outputs["overview"] = collect_overview(page, output_dir)
    if target in ("activity", "all"):
        outputs["activity"] = collect_table_page(page, MENU_ACTIVITY, "activity_alarm", output_dir)
    if target in ("history", "all"):
        outputs["history"] = collect_table_page(page, MENU_HISTORY, "history_alarm", output_dir)
    return outputs


def list_alarm_rules(page, rule_page: str, output: Path | None = None) -> dict:
    open_alarm_page(page, MENU_RULE)
    if not click_text(page, rule_page):
        raise RuntimeError(f"Alarm rule page not found: {rule_page}")
    wait(page, 2000)
    result = extract_table_rows(page, ["规则名称"])
    result["menu"] = MENU_RULE
    result["rule_page"] = rule_page
    result["url"] = page.url
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def write_alarm_rule(page, task: dict, debug_dir: Path) -> dict:
    open_alarm_page(page, MENU_RULE)
    if not click_text(page, task["rule_page"]):
        raise RuntimeError(f"Alarm rule page not found: {task['rule_page']}")
    if not click_text(page, BUTTON_ADD):
        raise RuntimeError("Add button not found.")
    wait(page, 2000)

    for label, value in task.get("fields", {}).items():
        fill_form_item(page, label, value)

    dump_debug(page, debug_dir / "alarm_rule_filled")
    submit = task.get("submit", False)
    if submit:
        if not click_text(page, BUTTON_CONFIRM):
            raise RuntimeError("Confirm button not found.")
        wait(page, 4000)
        dump_debug(page, debug_dir / "alarm_rule_after_submit")

    body = page.locator("body").inner_text(timeout=10000)
    return {
        "rule_page": task["rule_page"],
        "submitted": submit,
        "tail_text": body[-3000:],
    }
