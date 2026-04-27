from __future__ import annotations

import csv
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .db import run_psql_query
from .inventory import resolve_network_element_spec
from .ui import click_popup_item, click_text, click_top_menu, dump_debug, open_home, wait


MENU_PERFORMANCE = "\u6027\u80fd"
PAGE_METRIC_MANAGEMENT = "\u6307\u6807\u7ba1\u7406"
PAGE_TASK = "\u6027\u80fd\u4efb\u52a1"
PAGE_MONITOR = "\u6027\u80fd\u76d1\u63a7"
PAGE_QUERY = "\u6027\u80fd\u67e5\u8be2"
PAGE_STATISTICS = "\u6027\u80fd\u7edf\u8ba1"

BUTTON_ADD = "\u65b0\u589e"
BUTTON_CREATE_MONITOR = "+\u65b0\u5efa\u76d1\u63a7"
BUTTON_CREATE_QUERY = "+\u65b0\u5efa\u67e5\u8be2"
BUTTON_NEXT = "\u4e0b\u4e00\u6b65"
BUTTON_EXPORT = "\u5bfc\u51fa"

TARGET_METRIC_MANAGEMENT = "metric-management"

FILTER_PLACEHOLDERS = {
    "网元类型": "网元类型",
    "测量对象类型": "测量对象类型",
    "功能子集": "功能子集",
    "指标类型": "指标类型",
}
SEARCH_PLACEHOLDERS = ["搜索指标编码/名称", "指标编码/名称", "指标编码", "指标名称"]


PAGE_CONFIGS = {
    "metric-management": {
        "menu": PAGE_METRIC_MANAGEMENT,
        "route_name": "metric_management",
        "expected_headers": [
            "\u5e8f\u53f7",
            "\u7f51\u5143\u7c7b\u578b",
            "\u6d4b\u91cf\u5bf9\u8c61\u7c7b\u578b",
            "\u529f\u80fd\u5b50\u96c6",
            "\u6307\u6807\u7f16\u7801",
            "\u6307\u6807\u540d\u79f0",
        ],
    },
    "task": {
        "menu": PAGE_TASK,
        "route_name": "perf_task",
        "expected_headers": [
            "\u8fd0\u884c\u72b6\u6001",
            "\u7f51\u5143\u540d\u79f0",
            "\u7f51\u5143\u7c7b\u578b",
            "\u6700\u540e\u4e00\u6b21\u91c7\u96c6\u5468\u671f",
        ],
    },
    "monitor": {
        "menu": PAGE_MONITOR,
        "route_name": "perf_monitor",
        "expected_headers": [
            "\u5e8f\u53f7",
            "\u4efb\u52a1\u540d\u79f0",
            "\u4efb\u52a1\u72b6\u6001",
            "\u7f51\u5143\u540d\u79f0",
        ],
    },
    "query": {
        "menu": PAGE_QUERY,
        "route_name": "perf_query",
        "expected_headers": [
            "\u5e8f\u53f7",
            "\u6a21\u677f\u540d\u79f0",
            "\u7f51\u5143\u7c7b\u578b",
            "\u7f51\u5143\u540d\u79f0",
        ],
    },
    "statistics": {
        "menu": PAGE_STATISTICS,
        "route_name": "perf_statistics",
        "expected_headers": [
            "\u5e8f\u53f7",
            "\u7f51\u5143\u7c7b\u578b",
            "\u7f51\u5143\u540d\u79f0",
            "\u7f51\u5143IP\u5730\u5740",
        ],
    },
}


def open_performance_page(page, menu_name: str) -> None:
    last_error = None
    for attempt in range(2):
        try:
            open_home(page)
            click_top_menu(page, MENU_PERFORMANCE)
            click_popup_item(page, menu_name)
            wait(page, 2500)
            return
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                dump_debug(page, Path("debug_perf_open") / "attempt_1")
                continue
            raise last_error


def write_csv(path: Path, rows: list[dict]) -> None:
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def extract_perf_page(page, expected_headers: list[str]) -> dict:
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
      function labelFor(el) {
        const item = el.closest('.el-form-item');
        if (item) {
          const label = item.querySelector('.el-form-item__label');
          if (label) return text(label);
        }
        const prev = el.previousElementSibling;
        if (prev) {
          const label = text(prev);
          if (label && label.length <= 40) return label;
        }
        return '';
      }
      function rowOperationInfo(td) {
        if (!td) return [];
        return Array.from(td.querySelectorAll('[title], button, a, i, span')).filter(visible).map(el => ({
          text: text(el),
          title: el.getAttribute('title') || '',
          className: el.className || ''
        })).filter(item => item.text || item.title);
      }

      const headers = Array.from(document.querySelectorAll('th')).filter(visible).map(text).filter(Boolean);
      const rows = Array.from(document.querySelectorAll('tbody tr, .el-table__body tr')).filter(visible).map(tr => {
        const cells = Array.from(tr.querySelectorAll('td'));
        return {
          cells: cells.map(td => text(td.querySelector('.cell') || td)),
          operations: rowOperationInfo(cells[cells.length - 1])
        };
      }).filter(row => row.cells.some(Boolean));

      const matchedTable = expectedHeaders.length
        ? expectedHeaders.every(header => headers.includes(header))
        : headers.length > 0;

      const inputs = Array.from(document.querySelectorAll('input, textarea')).filter(visible).map(el => ({
        label: labelFor(el),
        tag: el.tagName.toLowerCase(),
        type: (el.getAttribute('type') || '').toLowerCase(),
        placeholder: el.getAttribute('placeholder') || '',
        value: el.value || '',
        readonly: !!el.readOnly,
        disabled: !!el.disabled,
        writeable: !el.readOnly && !el.disabled,
      }));

      const selects = Array.from(document.querySelectorAll('.el-select')).filter(visible).map(el => ({
        label: labelFor(el),
        text: text(el),
        writeable: !el.classList.contains('is-disabled')
      }));

      const buttons = Array.from(document.querySelectorAll('button, .el-button, a')).filter(visible).map(el => ({
        text: text(el),
        disabled: !!el.disabled || el.classList.contains('is-disabled')
      })).filter(item => item.text && item.text.length <= 30);

      const charts = Array.from(document.querySelectorAll('.echarts, canvas')).filter(visible).map(el => ({
        tag: el.tagName.toLowerCase(),
        id: el.id || '',
        className: el.className || ''
      }));

      return {
        url: location.href,
        matched_table: matchedTable,
        body_preview: text(document.body).slice(0, 4000),
        inputs,
        selects,
        buttons,
        table_headers: headers,
        table_rows: rows,
        charts,
      };
    }
    """
    return page.evaluate(js, {"expectedHeaders": expected_headers})


def extract_pagination_info(page) -> dict:
    js = """
    () => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }
      function text(el) {
        return (el?.innerText || el?.textContent || '').trim().replace(/\\s+/g, ' ');
      }
      const pager = Array.from(document.querySelectorAll('.el-pagination')).find(visible);
      if (!pager) return { present: false, total: 0, page_size: 0, current_page: 1, page_count: 1 };

      const totalText = text(pager.querySelector('.el-pagination__total'));
      const totalMatch = totalText.match(/(\\d+)/);
      const total = totalMatch ? Number(totalMatch[1]) : 0;

      const sizeInput = pager.querySelector('.el-pagination__sizes input');
      const sizeText = sizeInput ? (sizeInput.value || sizeInput.getAttribute('value') || '') : '';
      const sizeMatch = String(sizeText).match(/(\\d+)/);
      const pageSize = sizeMatch ? Number(sizeMatch[1]) : 20;

      const active = pager.querySelector('.el-pager li.active');
      const editor = pager.querySelector('.el-pagination__editor input');
      const currentText = active ? text(active) : (editor ? (editor.value || '') : '');
      const currentMatch = String(currentText).match(/(\\d+)/);
      const currentPage = currentMatch ? Number(currentMatch[1]) : 1;

      const pageNumbers = Array.from(pager.querySelectorAll('.el-pager li'))
        .map(text)
        .map(v => Number(v))
        .filter(v => !Number.isNaN(v));
      const maxPager = pageNumbers.length ? Math.max(...pageNumbers) : 1;
      const pageCount = Math.max(maxPager, pageSize > 0 && total > 0 ? Math.ceil(total / pageSize) : 1);

      return {
        present: true,
        total,
        page_size: pageSize,
        current_page: currentPage,
        page_count: pageCount
      };
    }
    """
    return page.evaluate(js)


def _wait_for_page_number(page, target_page: int) -> None:
    last_seen = None
    for _ in range(20):
        info = extract_pagination_info(page)
        last_seen = info.get("current_page")
        if last_seen == target_page:
            wait(page, 1200)
            return
        wait(page, 500)
    raise RuntimeError(f"Pagination did not reach page {target_page}, last seen page {last_seen}")


def goto_pagination_page(page, target_page: int) -> bool:
    info = extract_pagination_info(page)
    if not info.get("present"):
        return target_page == 1
    if target_page < 1 or target_page > info.get("page_count", 1):
        return False
    if info.get("current_page") == target_page:
        return True

    js_click_number = """
    ({ targetPage }) => {
      function text(el) {
        return (el?.innerText || el?.textContent || '').trim().replace(/\\s+/g, ' ');
      }
      const node = Array.from(document.querySelectorAll('.el-pagination .el-pager li.number'))
        .find(el => text(el) === String(targetPage));
      if (!node) return false;
      node.click();
      return true;
    }
    """
    try:
        if page.evaluate(js_click_number, {"targetPage": target_page}):
            _wait_for_page_number(page, target_page)
            return True
    except Exception:
        pass

    current_page = info.get("current_page", 1)
    if target_page > current_page:
        js_click_next = """
        () => {
          const btn = document.querySelector('.el-pagination button.btn-next');
          if (!btn || btn.disabled) return false;
          btn.click();
          return true;
        }
        """
        try:
            while current_page < target_page:
                if not page.evaluate(js_click_next):
                    break
                current_page += 1
                _wait_for_page_number(page, current_page)
            if current_page == target_page:
                return True
        except Exception:
            pass

    editor = page.locator(".el-pagination__editor input:visible").first
    try:
        editor.wait_for(state="visible", timeout=3000)
        editor.fill(str(target_page))
        editor.press("Enter")
        _wait_for_page_number(page, target_page)
        return True
    except Exception:
        return False


def _rows_to_dicts(headers: list[str], page_rows: list[dict]) -> list[dict]:
    rows = []
    for item in page_rows:
        cells = _align_cells_with_headers(item.get("cells", []), headers)
        row = {headers[idx]: value for idx, value in enumerate(cells[: len(headers)])}
        operations = item.get("operations") or []
        if operations:
            row["_operations"] = " | ".join(
                op.get("title") or op.get("text") or op.get("className", "") for op in operations
            )
        rows.append(row)
    return rows


def collect_perf_page(page, target: str, output_dir: Path | None = None) -> dict:
    if target not in PAGE_CONFIGS:
        raise RuntimeError(f"Unsupported performance page target: {target}")
    cfg = PAGE_CONFIGS[target]
    open_performance_page(page, cfg["menu"])
    result = extract_perf_page(page, cfg["expected_headers"])
    pagination = extract_pagination_info(page)
    all_page_rows = list(result.get("table_rows", []))
    page_summaries = [{"page": 1, "row_count": len(result.get("table_rows", []))}]
    if pagination.get("present") and pagination.get("page_count", 1) > 1:
        for page_no in range(2, pagination["page_count"] + 1):
            if not goto_pagination_page(page, page_no):
                raise RuntimeError(f"Unable to navigate to performance page {page_no}/{pagination['page_count']}")
            page_result = extract_perf_page(page, cfg["expected_headers"])
            all_page_rows.extend(page_result.get("table_rows", []))
            page_summaries.append({"page": page_no, "row_count": len(page_result.get("table_rows", []))})
        goto_pagination_page(page, 1)

    headers = result.get("table_headers", [])
    all_rows_dict = _rows_to_dicts(headers, all_page_rows)
    result["target"] = target
    result["menu"] = cfg["menu"]
    result["pagination"] = pagination
    result["page_summaries"] = page_summaries
    result["table_rows_all_pages"] = all_page_rows
    result["table_row_dicts_all_pages"] = all_rows_dict
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / f"{cfg['route_name']}.json"
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if all_rows_dict:
            write_csv(output_dir / f"{cfg['route_name']}.csv", all_rows_dict)
    return result


def collect_perf_data(page, target: str, output_dir: Path) -> dict:
    targets = list(PAGE_CONFIGS) if target == "all" else [target]
    outputs = {}
    for item in targets:
        outputs[item] = collect_perf_page(page, item, output_dir)
    summary = output_dir / "perf_export_summary.json"
    summary.write_text(json.dumps(outputs, ensure_ascii=False, indent=2), encoding="utf-8")
    return outputs


def _set_input_value(page, label: str, value) -> bool:
    js = """
    ({ labelText, value }) => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }
      function normalize(text) {
        return String(text || '').trim().replace(/\\s+/g, ' ');
      }
      function candidates() {
        const out = [];
        for (const item of document.querySelectorAll('.el-form-item')) {
          if (!visible(item)) continue;
          const label = item.querySelector('.el-form-item__label');
          const labelTextCurrent = normalize(label ? label.innerText || label.textContent || '' : '');
          if (!labelTextCurrent.includes(labelText)) continue;
          for (const input of item.querySelectorAll('input, textarea')) {
            if (visible(input)) out.push(input);
          }
        }
        return out;
      }
      const items = candidates();
      if (!items.length) return false;
      const input = items.find(el => !el.readOnly && !el.disabled) || null;
      if (!input) return false;
      input.focus();
      input.value = '';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.value = String(value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
    """
    return bool(page.evaluate(js, {"labelText": label, "value": value}))


def _open_select_by_label(page, label: str) -> bool:
    js = """
    ({ labelText }) => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }
      function normalize(text) {
        return String(text || '').trim().replace(/\\s+/g, ' ');
      }
      for (const item of document.querySelectorAll('.el-form-item')) {
        if (!visible(item)) continue;
        const label = item.querySelector('.el-form-item__label');
        const current = normalize(label ? label.innerText || label.textContent || '' : '');
        if (!current.includes(labelText)) continue;
        const select = item.querySelector('.el-select');
        if (select && visible(select)) {
          select.click();
          return true;
        }
      }
      return false;
    }
    """
    return bool(page.evaluate(js, {"labelText": label}))


def _select_dropdown_option(page, value) -> bool:
    js = """
    ({ target }) => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }
      function normalize(text) {
        return String(text || '').trim().replace(/\\s+/g, ' ');
      }
      const nodes = Array.from(
        document.querySelectorAll(
          '.el-select-dropdown__item, .el-popper li, .el-popper .el-tree-node__content, .el-popper .el-select-dropdown__item'
        )
      ).filter(visible);
      const normalizedTarget = normalize(target);
      const matches = nodes
        .map(node => ({ node, text: normalize(node.innerText || node.textContent || '') }))
        .filter(item => item.text === normalizedTarget || item.text.includes(normalizedTarget));
      if (!matches.length) return false;
      matches.sort((a, b) => a.text.length - b.text.length);
      matches[0].node.click();
      return true;
    }
    """
    if not page.evaluate(js, {"target": str(value)}):
        return False
    wait(page, 1000)
    return True


def fill_perf_form(page, fields: dict[str, object]) -> None:
    for label, value in fields.items():
        if _set_input_value(page, label, value):
            wait(page, 500)
            continue
        if _open_select_by_label(page, label) and _select_dropdown_option(page, value):
            if label in {"\u7f51\u5143\u7c7b\u578b", "\u6d4b\u91cf\u5bf9\u8c61\u7c7b\u578b", "\u529f\u80fd\u5b50\u96c6"}:
                wait(page, 1500)
            continue
        raise RuntimeError(f"Performance field not found or unsupported: {label}")


def open_metric_creation(page) -> None:
    open_performance_page(page, PAGE_METRIC_MANAGEMENT)
    if not click_text(page, BUTTON_ADD):
        raise RuntimeError("Metric add button not found.")
    wait(page, 1500)


def _set_metric_select(page, label: str, value: str) -> bool:
    js = """
    ({ labelText, targetValue }) => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }
      function normalize(text) {
        return String(text || '').trim().replace(/\\s+/g, ' ');
      }
      const item = Array.from(document.querySelectorAll('.el-form-item')).find(node => {
        const label = node.querySelector('.el-form-item__label');
        return normalize(label ? label.innerText || label.textContent || '' : '') === labelText;
      });
      if (!item) return { ok: false, reason: 'form-item-not-found' };
      const select = item.querySelector('.el-select');
      if (!select) return { ok: false, reason: 'select-not-found' };
      select.click();
      const options = Array.from(item.querySelectorAll('.el-select-dropdown__item, .el-popper .el-select-dropdown__item'));
      const target = options.find(opt => normalize(opt.innerText || opt.textContent || '') === targetValue);
      if (!target) {
        return {
          ok: false,
          reason: 'option-not-found',
          options: options.map(opt => normalize(opt.innerText || opt.textContent || '')).filter(Boolean)
        };
      }
      target.click();
      const input = item.querySelector('input.el-input__inner');
      return { ok: true, current: input ? input.value || '' : '' };
    }
    """
    result = page.evaluate(js, {"labelText": label, "targetValue": value})
    wait(page, 800)
    return bool(result.get("ok"))


def _set_metric_formula(page, value: str) -> bool:
    js = """
    ({ targetValue }) => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }
      const item = Array.from(document.querySelectorAll('.el-form-item')).find(node => {
        const label = node.querySelector('.el-form-item__label');
        const text = String(label ? label.innerText || label.textContent || '' : '').trim().replace(/\\s+/g, ' ');
        return text === '公式';
      });
      if (!item) return false;
      const textareas = Array.from(item.querySelectorAll('textarea')).filter(visible);
      const target = textareas[textareas.length - 1];
      if (!target) return false;
      target.focus();
      target.value = '';
      target.dispatchEvent(new Event('input', { bubbles: true }));
      target.value = String(targetValue);
      target.dispatchEvent(new Event('input', { bubbles: true }));
      target.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
    """
    ok = bool(page.evaluate(js, {"targetValue": value}))
    wait(page, 500)
    return ok


def fill_metric_form(page, fields: dict[str, object]) -> None:
    select_labels = {"数据类型", "网元类型", "测量对象类型", "功能子集"}
    for label, value in fields.items():
        if label == "公式":
            if not _set_metric_formula(page, str(value)):
                raise RuntimeError("Metric formula field not found.")
            continue
        if label in select_labels:
            if not _set_metric_select(page, label, str(value)):
                raise RuntimeError(f"Metric select field not found or option unavailable: {label}={value}")
            if label in {"网元类型", "测量对象类型"}:
                wait(page, 1500)
            continue
        if not _set_input_value(page, label, value):
            raise RuntimeError(f"Metric input field not found: {label}")
        wait(page, 500)


def create_metric_task(page, task: dict, debug_dir: Path) -> dict:
    open_metric_creation(page)
    fill_metric_form(page, task.get("fields", {}))
    dump_debug(page, debug_dir / "metric_filled")
    submit = bool(task.get("submit", False))
    if submit:
        if not click_text(page, "\u521b\u5efa"):
            raise RuntimeError("Metric create button not found.")
        wait(page, 3000)
        dump_debug(page, debug_dir / "metric_after_submit")
    return {
        "mode": "metric-create",
        "submitted": submit,
        "url": page.url,
        "tail_text": page.locator("body").inner_text(timeout=10000)[-3000:],
    }


def _open_named_creation(page, page_name: str, button_name: str) -> None:
    open_performance_page(page, page_name)
    if not click_text(page, button_name):
        raise RuntimeError(f"Create button not found: {button_name}")
    wait(page, 1500)


def _extract_wizard_state(page) -> dict:
    js = """
    () => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }
      function text(el) {
        return (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
      }
      function labelFor(el) {
        const item = el.closest('.el-form-item');
        if (item) {
          const label = item.querySelector('.el-form-item__label');
          if (label) return text(label);
        }
        return '';
      }
      return {
        url: location.href,
        body_preview: text(document.body).slice(0, 4000),
        inputs: Array.from(document.querySelectorAll('input, textarea')).filter(visible).map(el => ({
          label: labelFor(el),
          placeholder: el.getAttribute('placeholder') || '',
          value: el.value || '',
          readonly: !!el.readOnly,
          disabled: !!el.disabled,
          writeable: !el.readOnly && !el.disabled
        })),
        selects: Array.from(document.querySelectorAll('.el-select')).filter(visible).map(el => ({
          label: labelFor(el),
          text: text(el)
        })),
        step_titles: Array.from(document.querySelectorAll('.el-step__title, .step-title')).filter(visible).map(text).filter(Boolean)
      };
    }
    """
    return page.evaluate(js)


def create_monitor_task(page, task: dict, debug_dir: Path) -> dict:
    _open_named_creation(page, PAGE_MONITOR, BUTTON_CREATE_MONITOR)
    fill_perf_form(page, task.get("fields", {}))
    dump_debug(page, debug_dir / "monitor_step1_filled")
    advance = bool(task.get("next_step", False))
    if advance:
        if not click_text(page, BUTTON_NEXT):
            raise RuntimeError("Monitor next-step button not found.")
        wait(page, 2500)
        dump_debug(page, debug_dir / "monitor_step2")
    return {
        "mode": "monitor-create",
        "advanced": advance,
        "state": _extract_wizard_state(page),
    }


def create_query_task(page, task: dict, debug_dir: Path) -> dict:
    _open_named_creation(page, PAGE_QUERY, BUTTON_CREATE_QUERY)
    fill_perf_form(page, task.get("fields", {}))
    dump_debug(page, debug_dir / "query_step1_filled")
    advance = bool(task.get("next_step", False))
    if advance:
        if not click_text(page, BUTTON_NEXT):
            raise RuntimeError("Query next-step button not found.")
        wait(page, 2500)
        dump_debug(page, debug_dir / "query_step2")
    return {
        "mode": "query-create",
        "advanced": advance,
        "state": _extract_wizard_state(page),
    }


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())


def _align_cells_with_headers(cells: list[str], headers: list[str]) -> list[str]:
    normalized = [_normalize_text(cell) for cell in cells]
    if len(normalized) == len(headers):
        return normalized
    if len(normalized) == len(headers) + 1 and normalized and not normalized[0]:
        return normalized[1:]
    while len(normalized) > len(headers) and normalized and not normalized[0]:
        normalized = normalized[1:]
    while len(normalized) > len(headers) and normalized and not normalized[-1]:
        normalized = normalized[:-1]
    return normalized[: len(headers)]


def _collect_current_perf_page(page, target: str, collect_all_pages: bool = True) -> dict:
    if target not in PAGE_CONFIGS:
        raise RuntimeError(f"Unsupported performance page target: {target}")
    cfg = PAGE_CONFIGS[target]
    result = extract_perf_page(page, cfg["expected_headers"])
    pagination = extract_pagination_info(page)
    all_page_rows = list(result.get("table_rows", []))
    page_summaries = [{"page": 1, "row_count": len(result.get("table_rows", []))}]
    if collect_all_pages and pagination.get("present") and pagination.get("page_count", 1) > 1:
        for page_no in range(2, pagination["page_count"] + 1):
            if not goto_pagination_page(page, page_no):
                raise RuntimeError(f"Unable to navigate to performance page {page_no}/{pagination['page_count']}")
            page_result = extract_perf_page(page, cfg["expected_headers"])
            all_page_rows.extend(page_result.get("table_rows", []))
            page_summaries.append({"page": page_no, "row_count": len(page_result.get("table_rows", []))})
        goto_pagination_page(page, 1)

    headers = result.get("table_headers", [])
    all_rows_dict = _rows_to_dicts(headers, all_page_rows)
    result["target"] = target
    result["menu"] = cfg["menu"]
    result["pagination"] = pagination
    result["page_summaries"] = page_summaries
    result["table_rows_all_pages"] = all_page_rows
    result["table_row_dicts_all_pages"] = all_rows_dict
    result["row_count"] = len(all_rows_dict)
    result["metric_codes"] = _extract_metric_codes(all_rows_dict)
    return result


def _set_input_by_placeholder(page, placeholder_text: str, value: object) -> bool:
    js = """
    ({ placeholderText, value }) => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }
      function normalize(text) {
        return String(text || '').trim().replace(/\\s+/g, ' ');
      }
      const input = Array.from(document.querySelectorAll('input, textarea'))
        .filter(visible)
        .find(el => normalize(el.getAttribute('placeholder') || '').includes(placeholderText) && !el.readOnly && !el.disabled);
      if (!input) return false;
      input.focus();
      input.value = '';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.value = String(value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
    """
    return bool(page.evaluate(js, {"placeholderText": placeholder_text, "value": value}))


def _open_select_by_placeholder(page, placeholder_text: str) -> bool:
    js = """
    ({ placeholderText }) => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }
      function normalize(text) {
        return String(text || '').trim().replace(/\\s+/g, ' ');
      }
      const input = Array.from(document.querySelectorAll('.el-select input, .el-input input'))
        .filter(visible)
        .find(el => normalize(el.getAttribute('placeholder') || '').includes(placeholderText));
      if (!input) return false;
      const target = input.closest('.el-select') || input.closest('.el-input') || input;
      target.click();
      return true;
    }
    """
    return bool(page.evaluate(js, {"placeholderText": placeholder_text}))


def _click_metric_search(page) -> bool:
    for text in ("搜索", "查询"):
        if click_text(page, text, exact=True):
            wait(page, 1500)
            return True

    js = """
    () => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }
      const buttons = Array.from(document.querySelectorAll('button, .el-button, .el-icon-search, i')).filter(visible);
      const target = buttons.find(el => {
        const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
        const cls = String(el.className || '');
        return text.includes('搜索') || text.includes('查询') || cls.includes('el-icon-search') || cls.includes('search');
      });
      if (!target) return false;
      const clickable = target.closest('button, .el-button, .el-input-group__append, .el-input__suffix') || target;
      clickable.click();
      return true;
    }
    """
    clicked = bool(page.evaluate(js))
    if clicked:
        wait(page, 1500)
    return clicked


def apply_metric_management_filters(page, filters: dict[str, object]) -> dict:
    applied = {}
    for label, placeholder in FILTER_PLACEHOLDERS.items():
        if label not in filters:
            continue
        value = filters[label]
        if not _open_select_by_placeholder(page, placeholder):
            raise RuntimeError(f"Metric filter select not found: {label}")
        wait(page, 800)
        selected = False
        for _ in range(3):
            if _select_dropdown_option(page, value):
                selected = True
                break
            wait(page, 500)
        if not selected:
            raise RuntimeError(f"Metric filter option not found: {label}={value}")
        applied[label] = value
        wait(page, 800)

    search_value = None
    for key in ("搜索词", "指标编码", "指标名称", "搜索指标编码/名称"):
        if key in filters:
            search_value = filters[key]
            break
    if search_value is not None:
        for placeholder in SEARCH_PLACEHOLDERS:
            if _set_input_by_placeholder(page, placeholder, search_value):
                applied["搜索词"] = search_value
                break
        else:
            raise RuntimeError("Metric search input not found.")

    if applied:
        _click_metric_search(page)
        wait(page, 1500)
    return applied


def _extract_metric_codes(rows: list[dict]) -> list[str]:
    codes = []
    for row in rows:
        for key in ("指标编码", "黄金指标编码", "指标编码/名称"):
            value = _normalize_text(row.get(key, ""))
            if value and re.fullmatch(r"[A-Za-z0-9_]+", value):
                if value not in codes:
                    codes.append(value)
                break
    return codes


def query_metric_management(page, task: dict, debug_dir: Path) -> dict:
    open_performance_page(page, PAGE_METRIC_MANAGEMENT)
    filters = dict(task.get("filters", {}))
    applied_filters = apply_metric_management_filters(page, filters)
    dump_debug(page, debug_dir / "metric_query_filled")
    result = _collect_current_perf_page(page, TARGET_METRIC_MANAGEMENT, collect_all_pages=bool(task.get("collect_all_pages", True)))
    dump_debug(page, debug_dir / "metric_query_result")

    baseline_comparison = None
    baseline_path = task.get("baseline_path")
    if baseline_path:
        baseline = json.loads(Path(str(baseline_path)).read_text(encoding="utf-8"))
        baseline_comparison = compare_metric_rows_with_baseline(
            result.get("table_row_dicts_all_pages", []),
            baseline,
        )

    return {
        "mode": "perf-metric-query",
        "filters": applied_filters,
        "matched_table": result.get("matched_table", False),
        "table_headers": result.get("table_headers", []),
        "table_row_dicts_all_pages": result.get("table_row_dicts_all_pages", []),
        "pagination": result.get("pagination", {}),
        "page_summaries": result.get("page_summaries", []),
        "row_count": result.get("row_count", 0),
        "metric_codes": result.get("metric_codes", []),
        "baseline_comparison": baseline_comparison,
        "debug_body_preview": result.get("body_preview", ""),
    }


def compare_metric_rows_with_baseline(rows: list[dict], baseline: dict) -> dict:
    compare_fields = baseline.get("compare_fields") or ["指标编码", "指标名称"]
    required_rows = baseline.get("required_rows") or []
    actual_map = {}
    for row in rows:
        code = _normalize_text(row.get("指标编码", ""))
        if code:
            actual_map[code] = row

    comparisons = []
    mismatch_count = 0
    for expected in required_rows:
        code = _normalize_text(expected.get("指标编码", ""))
        actual = actual_map.get(code)
        field_results = []
        status = "match"
        for field in compare_fields:
            expected_value = _normalize_text(expected.get(field, ""))
            actual_value = _normalize_text(actual.get(field, "")) if actual else ""
            field_status = "match" if actual and expected_value == actual_value else "mismatch"
            if not actual:
                field_status = "missing"
            if field_status != "match":
                status = field_status if status == "match" else status
            field_results.append(
                {
                    "field": field,
                    "expected_value": expected_value,
                    "actual_value": actual_value,
                    "status": field_status,
                }
            )
        if status != "match":
            mismatch_count += 1
        comparisons.append({"metric_code": code, "status": status, "field_results": field_results})

    return {
        "required_count": len(required_rows),
        "compared_count": len(comparisons),
        "mismatch_count": mismatch_count,
        "all_match": mismatch_count == 0 and len(comparisons) > 0,
        "comparisons": comparisons,
    }


def check_metric_management_pagination(page, task: dict, debug_dir: Path) -> dict:
    open_performance_page(page, PAGE_METRIC_MANAGEMENT)
    applied_filters = apply_metric_management_filters(page, dict(task.get("filters", {})))
    first_page = extract_perf_page(page, PAGE_CONFIGS[TARGET_METRIC_MANAGEMENT]["expected_headers"])
    info = extract_pagination_info(page)
    next_ok = False
    prev_ok = False
    second_page_preview = []
    if info.get("page_count", 1) > 1:
        next_ok = goto_pagination_page(page, 2)
        second_page = extract_perf_page(page, PAGE_CONFIGS[TARGET_METRIC_MANAGEMENT]["expected_headers"])
        second_page_preview = second_page.get("table_rows", [])[:3]
        prev_ok = goto_pagination_page(page, 1)
    dump_debug(page, debug_dir / "metric_pagination")
    return {
        "mode": "perf-pagination-check",
        "filters": applied_filters,
        "pagination": info,
        "next_ok": next_ok if info.get("page_count", 1) > 1 else True,
        "previous_ok": prev_ok if info.get("page_count", 1) > 1 else True,
        "first_page_row_count": len(first_page.get("table_rows", [])),
        "second_page_preview": second_page_preview,
    }


def _read_csv_rows(path: Path) -> list[dict]:
    for encoding in ("utf-8-sig", "gbk", "utf-8"):
        try:
            with path.open("r", encoding=encoding, newline="") as file_obj:
                return list(csv.DictReader(file_obj))
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Unable to decode CSV export: {path}")


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    return ["".join(node.itertext()) for node in root.findall(".//a:si", ns)]


def _xlsx_sheet_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as zf:
        shared = _xlsx_shared_strings(zf)
        root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: list[list[str]] = []
    for row in root.findall(".//a:sheetData/a:row", ns):
        values = []
        for cell in row.findall("a:c", ns):
            cell_type = cell.get("t", "")
            value = ""
            if cell_type == "inlineStr":
                text_nodes = cell.findall(".//a:is//a:t", ns)
                value = "".join(node.text or "" for node in text_nodes)
            else:
                value_el = cell.find("a:v", ns)
                value = value_el.text if value_el is not None and value_el.text is not None else ""
            if cell_type == "s" and value:
                index = int(value)
                value = shared[index] if 0 <= index < len(shared) else ""
            values.append(_normalize_text(value))
        rows.append(values)
    return rows


def _read_xlsx_rows(path: Path) -> list[dict]:
    rows = _xlsx_sheet_rows(path)
    if not rows:
        return []
    headers = rows[0]
    return [dict(zip(headers, row[: len(headers)])) for row in rows[1:] if any(_normalize_text(item) for item in row)]


def read_export_rows(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _read_csv_rows(path)
    if suffix == ".xlsx":
        return _read_xlsx_rows(path)
    raise RuntimeError(f"Unsupported export file type: {path.suffix}")


def _normalize_row_subset(row: dict, compare_fields: list[str]) -> tuple[str, ...]:
    return tuple(_normalize_text(row.get(field, "")) for field in compare_fields)


def verify_metric_export(page, task: dict, debug_dir: Path) -> dict:
    open_performance_page(page, PAGE_METRIC_MANAGEMENT)
    applied_filters = apply_metric_management_filters(page, dict(task.get("filters", {})))
    web_result = _collect_current_perf_page(page, TARGET_METRIC_MANAGEMENT, collect_all_pages=bool(task.get("collect_all_pages", True)))
    compare_fields = task.get("compare_fields") or ["指标编码", "指标名称", "网元类型", "测量对象类型"]

    download_dir = debug_dir / "downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    if not click_text(page, BUTTON_EXPORT, exact=True):
        raise RuntimeError("Metric export button not found.")
    wait(page, 1000)
    with page.expect_response(
        lambda resp: "/api/web/pm/metrics/download" in resp.url,
        timeout=int(task.get("download_timeout_ms", 30000)),
    ) as response_info:
        if not click_text(page, "确定", exact=True):
            raise RuntimeError("Metric export confirm button not found.")
    response = response_info.value
    body = response.body()
    download_path = download_dir / _build_export_filename(response.headers, body)
    download_path.write_bytes(body)
    export_rows = read_export_rows(download_path)
    dump_debug(page, debug_dir / "metric_export")

    web_keys = [_normalize_row_subset(row, compare_fields) for row in web_result.get("table_row_dicts_all_pages", [])]
    export_keys = [_normalize_row_subset(row, compare_fields) for row in export_rows]
    missing_from_export = [key for key in web_keys if key not in export_keys]
    extra_in_export = [key for key in export_keys if key not in web_keys]
    return {
        "mode": "perf-export-verify",
        "filters": applied_filters,
        "download_path": str(download_path),
        "compare_fields": compare_fields,
        "web_row_count": len(web_keys),
        "export_row_count": len(export_keys),
        "missing_from_export": missing_from_export,
        "extra_in_export": extra_in_export,
        "summary": {
            "all_match": not missing_from_export and not extra_in_export and len(web_keys) == len(export_keys),
            "missing_count": len(missing_from_export),
            "extra_count": len(extra_in_export),
        },
    }


def _build_export_filename(headers: dict[str, str], body: bytes) -> str:
    content_disposition = headers.get("content-disposition", "")
    filename_match = re.search(r'filename="?([^";]+)"?', content_disposition, flags=re.IGNORECASE)
    if filename_match:
        return filename_match.group(1)
    if body[:2] == b"PK":
        return "metric_export.xlsx"
    return "metric_export.csv"


def _resolve_perf_ne(task: dict) -> tuple[dict | None, str | None]:
    if "network_element" not in task and "network_element_ref" not in task:
        return None, None
    spec = resolve_network_element_spec(task)
    return spec, str(spec.get("ip") or task.get("network_element") or "").strip() or None


def _safe_sql_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise RuntimeError(f"Unsafe SQL identifier: {value}")
    return value


def _perf_db_compare_metadata(task: dict) -> dict:
    db_config = dict(task["db"])
    metric_codes = task.get("metric_codes") or _extract_metric_codes(task.get("web_rows", []))
    if not metric_codes:
        raise RuntimeError("No metric codes provided for pm-metrics-metadata compare.")
    schema = _safe_sql_identifier(str(db_config.get("schema", "ems_upf")))
    compare_fields = task.get("compare_fields") or ["指标编码", "指标名称", "单位"]
    code_literals = ", ".join(f"'{str(code).lower()}'" for code in metric_codes)
    sql = f"""
SELECT DISTINCT ON (lower(metrics_code))
  metrics_code,
  metrics_name,
  metrics_desc,
  unit
FROM {schema}.pm_metrics
WHERE lower(metrics_code) IN ({code_literals})
  AND COALESCE(is_deleted, false) = false
ORDER BY lower(metrics_code), update_time DESC NULLS LAST, insert_time DESC NULLS LAST;
""".strip()
    raw = run_psql_query(db_config, sql)
    db_rows = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        code, name, desc, unit = (line.split("|", 3) + ["", "", "", ""])[:4]
        db_rows.append({"指标编码": code, "指标名称": name, "指标描述": desc, "单位": unit})
    db_map = {_normalize_text(row["指标编码"]): row for row in db_rows}

    comparisons = []
    mismatch_count = 0
    for code in metric_codes:
        key = _normalize_text(code)
        web_row = next((row for row in task.get("web_rows", []) if _normalize_text(row.get("指标编码", "")) == key), {})
        db_row = db_map.get(key)
        field_results = []
        status = "match"
        for field in compare_fields:
            web_value = _normalize_text(web_row.get(field, "")) if web_row else ""
            db_value = _normalize_text(db_row.get(field, "")) if db_row else ""
            field_status = "match" if db_row and web_value == db_value else "mismatch"
            if not db_row:
                field_status = "db_row_missing"
            if field_status != "match":
                status = field_status if status == "match" else status
            field_results.append(
                {
                    "field": field,
                    "web_value": web_value,
                    "db_value": db_value,
                    "status": field_status,
                }
            )
        if status != "match":
            mismatch_count += 1
        comparisons.append(
            {
                "metric_code": key,
                "status": status,
                "web_row": {field: _normalize_text(web_row.get(field, "")) for field in compare_fields},
                "db_row": {field: _normalize_text(db_row.get(field, "")) for field in compare_fields} if db_row else {},
                "field_results": field_results,
            }
        )
    return {
        "source": "pm-metrics-metadata",
        "metric_codes": metric_codes,
        "db_rows": db_rows,
        "comparisons": comparisons,
        "summary": {
            "compared_row_count": len(comparisons),
            "mismatch_row_count": mismatch_count,
            "all_match": mismatch_count == 0 and len(comparisons) > 0,
        },
    }


def _perf_db_compare_timeseries(task: dict) -> dict:
    db_config = dict(task["db"])
    schema = _safe_sql_identifier(str(db_config.get("schema", "ems_upf")))
    table = _safe_sql_identifier(str(task.get("table", "")))
    time_column = _safe_sql_identifier(str(task.get("time_column", "start_time")))
    metric_codes = [str(code).lower() for code in (task.get("metric_codes") or [])]
    if not table or not metric_codes:
        raise RuntimeError("timeseries-non-null compare requires task.table and task.metric_codes.")
    for code in metric_codes:
        _safe_sql_identifier(code)

    lookback_hours = int(task.get("lookback_hours", 24))
    metric_count_sql = ", ".join(f"COUNT({code}) AS {code}" for code in metric_codes)
    joins = ""
    where_parts = [f"t.{time_column} >= now() - interval '{lookback_hours} hours'"]
    _, ne_ip = _resolve_perf_ne(task)
    if ne_ip:
        escaped_ne_ip = ne_ip.replace("'", "''")
        joins = f" JOIN {schema}.cm_ne n ON n.id = t.ne_id "
        where_parts.append(f"n.ne_ip = '{escaped_ne_ip}'")
    where_clause = " AND ".join(where_parts)
    sql = f"""
SELECT COUNT(*) AS row_count,
       COUNT(DISTINCT t.{time_column}) AS point_count,
       MIN(t.{time_column}) AS min_time,
       MAX(t.{time_column}) AS max_time,
       {metric_count_sql}
FROM {schema}.{table} t
{joins}
WHERE {where_clause};
""".strip()
    raw = run_psql_query(db_config, sql).strip()
    columns = ["row_count", "point_count", "min_time", "max_time", *metric_codes]
    values = (raw.split("|") + [""] * len(columns))[: len(columns)]
    data = dict(zip(columns, values))
    metric_counts = {code: int(data.get(code) or 0) for code in metric_codes}
    min_point_count = int(task.get("min_point_count", 1))
    return {
        "source": "timeseries-non-null",
        "table": table,
        "metric_codes": metric_codes,
        "lookback_hours": lookback_hours,
        "row_count": int(data.get("row_count") or 0),
        "point_count": int(data.get("point_count") or 0),
        "min_time": data.get("min_time", ""),
        "max_time": data.get("max_time", ""),
        "metric_non_null_counts": metric_counts,
        "summary": {
            "all_metrics_have_data": all(count >= min_point_count for count in metric_counts.values()),
            "min_point_count": min_point_count,
        },
    }


def compare_perf_with_db(page, task: dict, debug_dir: Path) -> dict:
    source = str(task.get("source", "pm-metrics-metadata"))
    dump_debug(page, debug_dir / "perf_db_compare_context")
    if source == "pm-metrics-metadata":
        return _perf_db_compare_metadata(task)
    if source == "timeseries-non-null":
        return _perf_db_compare_timeseries(task)
    raise RuntimeError(f"Unsupported performance db compare source: {source}")
