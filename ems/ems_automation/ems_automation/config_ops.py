from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from .db import run_psql_query
from .inventory import resolve_network_element_spec
from .pages.command_processing_page import CommandProcessingPage
from .ui import dump_debug, wait, wait_for_condition


MENU_CONFIG = "\u914d\u7f6e"
MENU_COMMAND_PROCESSING = "\u547d\u4ee4\u5904\u7406"
BUTTON_EXECUTE = "\u6267\u884c"
ALLOWED_COMMAND_PREFIXES = ("SHOW ", "ADD ", "SET ", "DEL ")
DNN_COMPARE_PATH = [
    "\u4e1a\u52a1\u5f00\u901a\u914d\u7f6e",
    "DNN\u914d\u7f6e",
    "DNN VRF\u6620\u5c04\u914d\u7f6e",
    "\u67e5\u8be2DNN VRF\u6620\u5c04\u914d\u7f6e(SHOW UPFDNNVRFCONF)",
]
DNN_COMPARE_COMMAND = "SHOW UPFDNNVRFCONF;"
VRF_COMPARE_PATH = [
    "业务开通配置",
    "VRF配置",
    "查询VRF配置(SHOW UPFVRFCONF)",
]
VRF_COMPARE_COMMAND = "SHOW UPFVRFCONF;"


def _resolve_ne(task: dict) -> tuple[dict, str]:
    spec = resolve_network_element_spec(task)
    ne_ip = str(spec.get("ip") or task.get("network_element") or "").strip()
    if not ne_ip:
        raise RuntimeError("Resolved network element has no ip. Add `ip` in inventory.json for DB-backed operations.")
    return spec, ne_ip


def _wait_for_query_result(page, timeout_ms: int, previous_tail: str = "", command_text: str | None = None) -> None:
    command_marker = (command_text or "").rstrip(";")
    try:
        wait_for_condition(
            page,
            """
            ({ previousTail, commandMarker }) => {
              const body = document.body?.innerText || '';
              if (commandMarker && body.includes(commandMarker)) return true;
              if (previousTail && !body.endsWith(previousTail) && body !== previousTail) return true;
              const markers = ['执行成功', '记录数'];
              return markers.some(item => body.includes(item)) && (!previousTail || !body.endsWith(previousTail));
            }
            """,
            timeout=max(1500, timeout_ms),
            polling=250,
            arg={"previousTail": previous_tail[-4000:], "commandMarker": command_marker},
        )
    except Exception:
        wait(page, min(timeout_ms, 1500))


def _wait_for_submit_settle(page, timeout_ms: int = 3000) -> None:
    try:
        wait_for_condition(
            page,
            """
            () => {
              const progress = document.querySelector('#nprogress .bar');
              const loadingMask = document.querySelector('.el-loading-mask');
              const progressHidden = !progress || getComputedStyle(progress).display === 'none';
              const loadingHidden = !loadingMask || getComputedStyle(loadingMask).display === 'none';
              return progressHidden && loadingHidden;
            }
            """,
            timeout=timeout_ms,
            polling=200,
        )
    except Exception:
        wait(page, 500)


def _command_text_from_path(path_by_name: list[str]) -> str | None:
    leaf_name = path_by_name[-1]
    if "(" in leaf_name and ")" in leaf_name:
        start = leaf_name.rfind("(")
        end = leaf_name.rfind(")")
        if start != -1 and end != -1 and end > start:
            return leaf_name[start + 1 : end].strip()
    return None


def _format_command_value(value: Any) -> str:
    text = str(value).strip()
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return text
    escaped = text.replace('"', '\\"')
    return f'"{escaped}"'


def _build_command_from_params(command_text: str | None, detected_fields: list[dict], params: dict) -> str | None:
    if not command_text:
        return None
    base = command_text.rstrip(";")
    if not params:
        return base + ";"

    field_name_map = {}
    for field in detected_fields:
        label = str(field.get("label", "")).strip()
        field_name = str(field.get("field_name", "")).strip()
        if label and field_name:
            field_name_map[label] = field_name

    fallback_map = {
        "DNN名称": "DNN",
        "VRF ID": "VRFID",
        "N3 VRFID": "N3VRF",
        "N6 VRFID": "N6VRF",
        "N9 VRFID": "N9VRF",
        "S1U VRFID": "S1UVRF",
    }

    parts = []
    for label, value in params.items():
        field_name = field_name_map.get(label) or fallback_map.get(label)
        if not field_name:
            continue
        parts.append(f"{field_name}={_format_command_value(value)}")
    if not parts:
        return base + ";"
    return base + ":" + ",".join(parts) + ";"


def open_command_processing(page, ne_ip: str) -> None:
    CommandProcessingPage(page).open(ne_ip)


def click_tree_path(page, path_by_name: list[str]) -> None:
    js = """
    ({ path, expand_only }) => {
      function normalizeName(name) {
        return String(name || '').trim().replace(/\\s+/g, ' ');
      }
      function nameCandidates(name) {
        const normalized = normalizeName(name);
        const out = [normalized];
        const bracketChars = ['(', ')', '（', '）'];
        let trimmed = normalized;
        for (const ch of bracketChars) {
          trimmed = trimmed.split(ch)[0].trim();
        }
        if (trimmed) out.push(normalizeName(trimmed));
        return Array.from(new Set(out.filter(Boolean)));
      }
      function textOfContent(content) {
        const label = content.querySelector('.custom-label') || content.querySelector('.el-tooltip') || content;
        return normalizeName(label.innerText || label.textContent || '');
      }
      function directChildNodes(container) {
        return Array.from(container.children).filter(el => el.classList.contains('el-tree-node'));
      }
      const tree = document.querySelector('.el-tree');
      if (!tree) return { ok: false, missing: '(tree)' };
      let container = tree;
      let found = null;
      for (const name of path) {
        const candidates = nameCandidates(name);
        found = null;
        for (const node of directChildNodes(container)) {
          const content = node.querySelector(':scope > .el-tree-node__content');
          if (!content) continue;
          const text = textOfContent(content);
          if (candidates.some(candidate => text === candidate || text.includes(candidate) || candidate.includes(text))) {
            found = node;
            break;
          }
        }
        if (!found) return { ok: false, missing: name };
        container = found.querySelector(':scope > .el-tree-node__children');
      }
      const content = found.querySelector(':scope > .el-tree-node__content');
      if (!content) return { ok: false, missing: path[path.length - 1] };
      content.scrollIntoView({ block: 'center' });
      content.click();
      return { ok: true };
    }
    """
    for idx in range(len(path_by_name)):
        prefix = path_by_name[: idx + 1]
        result = page.evaluate(js, {"path": prefix, "expand_only": idx != len(path_by_name) - 1})
        if not result["ok"]:
            raise RuntimeError(f"Unable to click tree node: {result['missing']}")
        wait(page, 1000 if idx == len(path_by_name) - 1 else 700)


def expand_all_tree_nodes(page, rounds: int = 6) -> None:
    js_expand = """
    () => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }
      const tree = document.querySelector('.el-tree');
      if (!tree) return { expanded: 0 };
      let expanded = 0;
      const nodes = Array.from(tree.querySelectorAll('.el-tree-node.is-focusable'));
      for (const node of nodes) {
        if (!visible(node) || node.classList.contains('is-expanded')) continue;
        const content = node.querySelector(':scope > .el-tree-node__content');
        if (!content || !visible(content)) continue;
        const icon = content.querySelector('i');
        const iconCls = icon ? icon.className : '';
        if (!iconCls.includes('icon-folder')) continue;
        content.scrollIntoView({ block: 'center' });
        content.click();
        expanded += 1;
      }
      return { expanded };
    }
    """
    js_scroll_top = """
    () => {
      const wrap =
        document.querySelector('.el-aside .el-scrollbar__wrap') ||
        document.querySelector('.mml-aside .el-scrollbar__wrap') ||
        document.querySelector('.el-aside') ||
        document.querySelector('.mml-aside');
      if (!wrap) return false;
      wrap.scrollTop = 0;
      return true;
    }
    """
    js_scroll_down = """
    () => {
      const wrap =
        document.querySelector('.el-aside .el-scrollbar__wrap') ||
        document.querySelector('.mml-aside .el-scrollbar__wrap') ||
        document.querySelector('.el-aside') ||
        document.querySelector('.mml-aside');
      if (!wrap) return null;
      const before = wrap.scrollTop;
      wrap.scrollTop = wrap.scrollTop + wrap.clientHeight - 40;
      return { before, after: wrap.scrollTop };
    }
    """
    for _ in range(rounds):
        page.evaluate(js_scroll_top)
        wait(page, 500)
        while True:
            result = page.evaluate(js_expand)
            if result["expanded"] > 0:
                wait(page, 800)
            info = page.evaluate(js_scroll_down)
            wait(page, 500)
            if not info or info["after"] == info["before"]:
                break
        page.evaluate(js_scroll_top)
        wait(page, 500)


def extract_tree_dom(page) -> list[dict]:
    js = """
    () => {
      function textOfContent(content) {
        const label = content.querySelector('.custom-label') || content.querySelector('.el-tooltip') || content;
        return (label.innerText || label.textContent || '').trim().replace(/\\s+/g, ' ');
      }
      function parseNode(node, path) {
        const content = node.querySelector(':scope > .el-tree-node__content');
        if (!content) return null;
        const text = textOfContent(content);
        if (!text) return null;
        const currentPath = [...path, text];
        const item = { name: text, path_by_name: currentPath, children: [] };
        if (text.includes('(') && text.includes(')')) {
          const start = text.lastIndexOf('(');
          const end = text.lastIndexOf(')');
          if (start !== -1 && end !== -1 && end > start) {
            item.command = text.slice(start + 1, end).trim();
          }
        }
        const childrenWrap = node.querySelector(':scope > .el-tree-node__children');
        if (childrenWrap) {
          const childNodes = Array.from(childrenWrap.children).filter(el => el.classList.contains('el-tree-node'));
          for (const child of childNodes) {
            const parsed = parseNode(child, currentPath);
            if (parsed) item.children.push(parsed);
          }
        }
        return item;
      }
      const tree = document.querySelector('.el-tree');
      if (!tree) return [];
      const roots = Array.from(tree.children).filter(el => el.classList.contains('el-tree-node'));
      const result = [];
      for (const root of roots) {
        const parsed = parseNode(root, []);
        if (parsed) result.push(parsed);
      }
      return result;
    }
    """
    return page.evaluate(js)


def extract_form_fields(page) -> list[dict]:
    js = """
    () => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }
      const result = [];
      const formItems = Array.from(document.querySelectorAll('.el-form-item'));
      let runningIndex = 0;
      for (const item of formItems) {
        if (!visible(item)) continue;
        const labelEl = item.querySelector('.el-form-item__label');
        const label = labelEl ? (labelEl.innerText || labelEl.textContent || '').trim().replace(/\\s+/g, ' ') : '';
        const inputs = Array.from(item.querySelectorAll('input, textarea'));
        const selects = Array.from(item.querySelectorAll('.el-select'));
        for (const el of inputs) {
          if (!visible(el)) continue;
          result.push({
            index: runningIndex,
            label: label,
            field_type: el.tagName.toLowerCase(),
            input_type: el.getAttribute('type') || '',
            placeholder: el.getAttribute('placeholder') || '',
            value: el.value || '',
            yaml_value: ''
          });
          runningIndex += 1;
        }
        for (const el of selects) {
          if (!visible(el)) continue;
          const inner = el.querySelector('input');
          result.push({
            index: runningIndex,
            label: label,
            field_type: 'select',
            input_type: '',
            placeholder: inner ? (inner.getAttribute('placeholder') || '') : '',
            value: inner ? (inner.value || '') : '',
            yaml_value: ''
          });
          runningIndex += 1;
        }
      }
      return result;
    }
    """
    return page.evaluate(js)


def extract_command_result(page, command_text: str | None = None) -> dict:
    body = page.locator("body").inner_text(timeout=10000)
    normalized = body.replace("\r\n", "\n")

    marker = None
    if command_text:
        normalized_command = command_text.rstrip(";")
        candidates = [
            f"銆怤O.1銆戯細{command_text}",
            f"銆怤O.1銆戯細{normalized_command}",
            command_text,
            normalized_command,
        ]
        for candidate in candidates:
            pos = normalized.find(candidate)
            if pos != -1:
                marker = candidate
                break

    start = normalized.find(marker) if marker else -1
    if start == -1:
        for fallback in ("銆怤O.1銆戯細", "鎿嶄綔缃戝厓锛?"):
            pos = normalized.find(fallback)
            if pos != -1:
                start = pos
                break

    if start == -1:
        return {
            "found": False,
            "command_text": command_text,
            "result_text": "",
            "tail_text": normalized[-5000:],
        }

    end = len(normalized)
    stop_markers = [
        "\n鎵ц\n",
        "\n娓呴櫎\n鎿嶄綔缃戝厓锛?",
        "\n鎿嶄綔缃戝厓锛?",
    ]
    for stop_marker in stop_markers:
        pos = normalized.find(stop_marker, start + 1)
        if pos != -1:
            end = min(end, pos)

    result_text = normalized[start:end].strip()
    lines = [line.rstrip() for line in result_text.split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and lines[-1].strip() in {"鎵ц", "娓呴櫎", "鏂囨湰缁撴灉鎿嶄綔璁板綍甯姪"}:
        lines.pop()
    result_text = "\n".join(lines).strip()
    return {
        "found": True,
        "command_text": command_text,
        "result_text": result_text,
        "tail_text": normalized[-5000:],
    }


def parse_command_table(result_text: str) -> dict:
    lines = [line.strip() for line in result_text.splitlines() if line.strip()]
    if not lines:
        return {"headers": [], "rows": []}

    header_index = -1
    for idx, line in enumerate(lines):
        columns = [item.strip() for item in line.split("\t") if item.strip()]
        if "\t" in line and ("鎿嶄綔缁存姢" in line or len(columns) >= 2):
            header_index = idx
            break
    if header_index == -1:
        return {"headers": [], "rows": []}

    headers = [item.strip() for item in lines[header_index].split("\t") if item.strip()]
    rows = []
    current = []
    stop_prefixes = ("璁板綍鏁帮細", "鎵ц鎴愬姛寮€濮嬫椂闂达細", "鑰楁椂锛?")

    for line in lines[header_index + 1 :]:
        if any(line.startswith(prefix) for prefix in stop_prefixes):
            break
        current.append(line)
        if len(current) == len(headers):
            rows.append(dict(zip(headers, current)))
            current = []

    return {"headers": headers, "rows": rows}


def filter_allowed_leaves(items: list[dict], acc: list[list[str]] | None = None) -> list[list[str]]:
    if acc is None:
        acc = []
    for item in items:
        children = item.get("children", [])
        if children:
            filter_allowed_leaves(children, acc)
        else:
            cmd = item.get("command", "")
            if any(cmd.startswith(prefix) for prefix in ALLOWED_COMMAND_PREFIXES):
                acc.append(item["path_by_name"])
    return acc


def attach_fields(items: list[dict], field_map: dict[str, list[dict]]) -> None:
    for item in items:
        key = " > ".join(item["path_by_name"])
        if key in field_map:
            item["fields"] = field_map[key]
        attach_fields(item.get("children", []), field_map)


def normalize_tree(items: list[dict], ne_ip: str, level: int = 0) -> list[dict]:
    out = []
    for item in items:
        node = {
            "name": item["name"],
            "level": level,
            "children": normalize_tree(item.get("children", []), ne_ip, level + 1),
        }
        if "command" in item:
            node["command"] = item["command"]
            node["execute_method"] = {
                "type": "command_processing",
                "network_element": ne_ip,
                "path_by_name": item["path_by_name"],
            }
        if "fields" in item:
            node["fields"] = item["fields"]
        out.append(node)
    return out


def export_config_tree(page, ne_ip: str, output: Path) -> dict:
    command_page = CommandProcessingPage(page)
    command_page.open(ne_ip)
    command_page.expand_all_tree_nodes(rounds=8)
    raw_tree = command_page.extract_tree_dom()
    leaf_paths = filter_allowed_leaves(raw_tree)
    field_map: dict[str, list[dict]] = {}

    for idx, path_by_name in enumerate(leaf_paths, 1):
        key = " > ".join(path_by_name)
        try:
            command_page.open(ne_ip)
            command_page.click_tree_path(path_by_name)
            wait(page, 1200)
            field_map[key] = command_page.extract_form_fields()
        except Exception:
            field_map[key] = []
            command_page.dump_debug(output.parent / f"config_tree_leaf_{idx:03d}")

    attach_fields(raw_tree, field_map)
    result = {
        "site": "https://127.0.0.1:50443",
        "entry": "https://127.0.0.1:50443/index#/topoOverview",
        "module": "\u914d\u7f6e -> \u547d\u4ee4\u5904\u7406",
        "network_element": ne_ip,
        "tree": normalize_tree(raw_tree, ne_ip, 0),
    }
    output.write_text(
        yaml.safe_dump(result, allow_unicode=True, sort_keys=False, width=200),
        encoding="utf-8",
    )
    return result


def inspect_config_task(page, task: dict, debug_dir: Path) -> dict:
    ne_spec, ne_ip = _resolve_ne(task)
    path_by_name = task["path_by_name"]
    command_page = CommandProcessingPage(page)
    detected_fields = command_page.open_form(ne_spec, path_by_name)
    command_page.dump_debug(debug_dir / "config_read")
    body = page.locator("body").inner_text(timeout=10000)
    return {
        "mode": "read",
        "path_by_name": path_by_name,
        "detected_fields": detected_fields,
        "tail_text": body[-5000:],
    }


def query_config_task(page, task: dict, debug_dir: Path) -> dict:
    ne_spec, ne_ip = _resolve_ne(task)
    path_by_name = task["path_by_name"]
    params = task.get("params", {})
    wait_ms = int(task.get("result_wait_ms", 4000))
    command_page = CommandProcessingPage(page)
    detected_fields = command_page.open_form(ne_spec, path_by_name)
    command_page.fill_form(params)
    before_body = page.locator("body").inner_text(timeout=10000)
    command_text = task.get("command_text") or _command_text_from_path(path_by_name)
    generated_command = _build_command_from_params(command_text, detected_fields, params)
    if generated_command:
        command_page.set_command_text(generated_command)

    command_page.dump_debug(debug_dir / "config_query_filled")
    command_page.execute()

    _wait_for_query_result(page, wait_ms, before_body, generated_command or command_text)
    command_page.dump_debug(debug_dir / "config_query_after_execute")

    result = extract_command_result(page, command_text)
    parsed = parse_command_table(result["result_text"])
    result.update(
        {
            "mode": "query",
            "path_by_name": path_by_name,
            "detected_fields": detected_fields,
            "submitted": True,
            "generated_command": generated_command,
            "headers": parsed["headers"],
            "rows": parsed["rows"],
        }
    )
    return result


def execute_config_task(page, task: dict, debug_dir: Path) -> dict:
    ne_spec, ne_ip = _resolve_ne(task)
    path_by_name = task["path_by_name"]
    params = task.get("params", {})
    command_page = CommandProcessingPage(page)
    detected_fields = command_page.open_form(ne_spec, path_by_name)
    command_page.fill_form(params)
    submit = task.get("submit", False)
    command_text = task.get("command_text") or _command_text_from_path(path_by_name)
    generated_command = _build_command_from_params(command_text, detected_fields, params)
    if generated_command:
        command_page.set_command_text(generated_command)

    command_page.dump_debug(debug_dir / "config_filled")
    if submit:
        before_body = page.locator("body").inner_text(timeout=10000)
        command_page.execute()
        _wait_for_query_result(page, int(task.get("submit_wait_ms", 5000)), before_body, generated_command or command_text)
        _wait_for_submit_settle(page, 1000)
        command_page.dump_debug(debug_dir / "config_after_execute")

    body = page.locator("body").inner_text(timeout=10000)
    return {
        "mode": "write",
        "path_by_name": path_by_name,
        "detected_fields": detected_fields,
        "submitted": submit,
        "generated_command": generated_command,
        "tail_text": body[-5000:],
    }


def _normalize_compare_key(value: str) -> str:
    return " ".join(str(value).strip().lower().replace("：", ":").split())


def _normalize_compare_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _default_dnn_compare_task(task: dict) -> dict:
    merged = dict(task)
    merged.setdefault("path_by_name", DNN_COMPARE_PATH)
    merged.setdefault("command_text", DNN_COMPARE_COMMAND)
    merged.setdefault("result_wait_ms", 4000)

    params = dict(merged.get("params", {}))
    dnn_name = merged.get("dnn_name")
    if dnn_name and "DNN名称" not in params:
        params["DNN名称"] = dnn_name
    merged["params"] = params
    return merged


def _load_db_dnn_rows(db_config: dict, ne_ip: str) -> list[dict[str, Any]]:
    source = str(db_config.get("source", "config_snapshot"))
    if source == "mml_exec_log_show":
        return _load_db_dnn_rows_from_mml_exec_log(db_config, ne_ip)

    schema = str(db_config.get("schema", "ems_upf"))
    ne_ip_escaped = ne_ip.replace("'", "''")
    sql = f"""
WITH target_ne AS (
  SELECT running_conf
  FROM {schema}.cm_ne
  WHERE ne_ip = '{ne_ip_escaped}'
    AND COALESCE(is_deleted, false) = false
  ORDER BY update_time DESC NULLS LAST
  LIMIT 1
)
SELECT COALESCE(((running_conf::jsonb)->'upf'->'upfDnnVrfConf')::text, '[]')
FROM target_ne;
""".strip()
    output = run_psql_query(db_config, sql)
    if not output:
        return []
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Unable to parse DNN JSON from database: {exc}") from exc
    if not isinstance(parsed, list):
        raise RuntimeError("Database DNN data is not a JSON array.")
    return [item for item in parsed if isinstance(item, dict)]


def _parse_dnn_show_result_text(result_text: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in result_text.splitlines() if line.strip()]
    header_index = -1
    for idx, line in enumerate(lines):
        if "DNN名称" in line and "N3 VRFID" in line and "S1U VRFID" in line:
            header_index = idx
            break
    if header_index == -1:
        return []

    rows = []
    for line in lines[header_index + 1 :]:
        if set(line) == {"-"}:
            continue
        if line.startswith("记录数：") or line.startswith("执行成功") or line.startswith("耗时："):
            break
        parts = re.split(r"\s{2,}", line)
        parts = [part.strip() for part in parts if part.strip()]
        if len(parts) >= 5:
            rows.append(
                {
                    "dnn": parts[0],
                    "n3Vrf": parts[1],
                    "n6Vrf": parts[2],
                    "n9Vrf": parts[3],
                    "s1uVrf": parts[4],
                }
            )
    return rows


def _load_db_dnn_rows_from_mml_exec_log(db_config: dict, ne_ip: str) -> list[dict[str, Any]]:
    schema = str(db_config.get("schema", "ems_upf"))
    ne_ip_escaped = ne_ip.replace("'", "''")
    dnn_name = str(db_config.get("dnn_name", "")).replace("'", "''")
    dnn_filter = f" AND mml_exec_command LIKE '%DNN=\"{dnn_name}\"%'" if dnn_name else ""
    sql = f"""
SELECT COALESCE(format_result, raw_result, '')
FROM {schema}.cm_mml_exec_log
WHERE success = true
  AND mml_exec_command LIKE 'SHOW UPFDNNVRFCONF%'
  AND COALESCE(format_result, raw_result, '') LIKE '%{ne_ip_escaped}%'
  {dnn_filter}
ORDER BY insert_time DESC
LIMIT 1;
""".strip()
    output = run_psql_query(db_config, sql)
    if not output:
        return []
    return _parse_dnn_show_result_text(output)


def _normalize_web_dnn_rows(rows: list[dict], dnn_name: str | None = None) -> list[dict]:
    field_aliases = {
        "dnn名称": "DNN名称",
        "dnn": "DNN名称",
        "n3 vrfid": "N3 VRFID",
        "n6 vrfid": "N6 VRFID",
        "n9 vrfid": "N9 VRFID",
        "s1u vrfid": "S1U VRFID",
    }
    normalized_rows = []
    for row in rows:
        normalized = {}
        for key, value in row.items():
            normalized[key] = _normalize_compare_value(value)
            key_norm = _normalize_compare_key(key)
            if key_norm in field_aliases:
                normalized[field_aliases[key_norm]] = _normalize_compare_value(value)
        if dnn_name and normalized.get("DNN名称", "") != dnn_name:
            continue
        normalized_rows.append(normalized)
    return normalized_rows


def _normalize_db_dnn_rows(rows: list[dict[str, Any]], dnn_name: str | None = None) -> tuple[list[dict], dict[str, dict]]:
    normalized_rows = []
    normalized_map = {}
    for row in rows:
        normalized = {
            "DNN名称": _normalize_compare_value(row.get("dnn")),
            "N3 VRFID": _normalize_compare_value(row.get("n3Vrf")),
            "N6 VRFID": _normalize_compare_value(row.get("n6Vrf")),
            "N9 VRFID": _normalize_compare_value(row.get("n9Vrf")),
            "S1U VRFID": _normalize_compare_value(row.get("s1uVrf")),
            "_db_source": row,
        }
        dnn_value = normalized["DNN名称"]
        if dnn_name and dnn_value != dnn_name:
            continue
        normalized_rows.append(normalized)
        if dnn_value:
            normalized_map[dnn_value] = normalized
    return normalized_rows, normalized_map


def _build_dnn_compare_rows(web_rows: list[dict], db_row_map: dict[str, dict]) -> list[dict]:
    compare_fields = ["DNN名称", "N3 VRFID", "N6 VRFID", "N9 VRFID", "S1U VRFID"]
    results = []
    for web_row in web_rows:
        dnn_name = web_row.get("DNN名称", "")
        db_row = db_row_map.get(dnn_name)
        field_results = []
        match_count = 0
        for field in compare_fields:
            web_value = _normalize_compare_value(web_row.get(field, ""))
            db_value = _normalize_compare_value(db_row.get(field, "")) if db_row else ""
            status = "match" if db_row and web_value == db_value else "mismatch"
            if not db_row:
                status = "db_row_missing"
            if status == "match":
                match_count += 1
            field_results.append(
                {
                    "field": field,
                    "web_value": web_value,
                    "db_value": db_value,
                    "status": status,
                }
            )
        results.append(
            {
                "dnn_name": dnn_name,
                "status": "match" if match_count == len(compare_fields) else "mismatch",
                "web_row": {field: _normalize_compare_value(web_row.get(field, "")) for field in compare_fields},
                "db_row": {field: _normalize_compare_value(db_row.get(field, "")) for field in compare_fields} if db_row else {},
                "field_results": field_results,
            }
        )
    return results


def compare_dnn_web_with_db(page, task: dict, debug_dir: Path) -> dict:
    merged_task = _default_dnn_compare_task(task)
    query_result = query_config_task(page, merged_task, debug_dir)

    db_config = merged_task.get("db")
    if not isinstance(db_config, dict):
        raise RuntimeError("task.db must be provided and must be an object.")

    dnn_name = merged_task.get("dnn_name")
    db_config = dict(db_config)
    if dnn_name:
        db_config.setdefault("dnn_name", dnn_name)
    web_rows = _normalize_web_dnn_rows(query_result.get("rows", []), dnn_name)
    _, ne_ip = _resolve_ne(merged_task)
    db_rows_raw = _load_db_dnn_rows(db_config, ne_ip)
    db_rows, db_row_map = _normalize_db_dnn_rows(db_rows_raw, dnn_name)
    comparisons = _build_dnn_compare_rows(web_rows, db_row_map)

    web_dnn_names = {row.get("DNN名称", "") for row in web_rows if row.get("DNN名称", "")}
    db_only_rows = [
        {key: value for key, value in row.items() if key != "_db_source"}
        for row in db_rows
        if row.get("DNN名称", "") not in web_dnn_names
    ]

    mismatch_row_count = sum(1 for item in comparisons if item["status"] != "match")
    return {
        "mode": "dnn_compare",
        "network_element": merged_task["network_element"],
        "path_by_name": merged_task["path_by_name"],
        "command_text": merged_task["command_text"],
        "dnn_name": dnn_name,
        "web_headers": query_result.get("headers", []),
        "web_rows": web_rows,
        "db_rows": [{key: value for key, value in row.items() if key != "_db_source"} for row in db_rows],
        "comparisons": comparisons,
        "db_only_rows": db_only_rows,
        "summary": {
            "web_row_count": len(web_rows),
            "db_row_count": len(db_rows),
            "compared_row_count": len(comparisons),
            "mismatch_row_count": mismatch_row_count,
            "all_match": mismatch_row_count == 0 and len(comparisons) > 0,
        },
        "debug": {
            "detected_fields": query_result.get("detected_fields", []),
            "tail_text": query_result.get("tail_text", ""),
        },
    }


def _normalize_web_dnn_rows(rows: list[dict], dnn_name: str | None = None) -> list[dict]:
    canonical_label = "\u0044\u004e\u004e\u540d\u79f0"
    field_aliases = {
        "dnn名称": canonical_label,
        "dnn": canonical_label,
        "n3 vrfid": "N3 VRFID",
        "n6 vrfid": "N6 VRFID",
        "n9 vrfid": "N9 VRFID",
        "s1u vrfid": "S1U VRFID",
    }
    normalized_rows = []
    for row in rows:
        normalized = {}
        for key, value in row.items():
            normalized[key] = _normalize_compare_value(value)
            key_norm = _normalize_compare_key(key)
            if key_norm in field_aliases:
                normalized[field_aliases[key_norm]] = _normalize_compare_value(value)
        if dnn_name and normalized.get(canonical_label, "") != dnn_name:
            continue
        normalized_rows.append(normalized)
    return normalized_rows


# Override the garbled helper implementations above with stable Unicode-safe versions.
def _normalize_compare_key(value: str) -> str:
    return " ".join(str(value).strip().lower().replace("\uff1a", ":").split())


def _default_dnn_compare_task(task: dict) -> dict:
    merged = dict(task)
    merged.setdefault("path_by_name", DNN_COMPARE_PATH)
    merged.setdefault("command_text", DNN_COMPARE_COMMAND)
    merged.setdefault("result_wait_ms", 4000)
    merged.setdefault("params", {})
    return merged


def _parse_dnn_show_result_text(result_text: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in result_text.splitlines() if line.strip()]
    header_index = -1
    for idx, line in enumerate(lines):
        if "\u0044\u004e\u004e\u540d\u79f0" in line and "N3 VRFID" in line and "S1U VRFID" in line:
            header_index = idx
            break
    if header_index == -1:
        return []

    rows = []
    for line in lines[header_index + 1 :]:
        if set(line) == {"-"}:
            continue
        if line.startswith("\u8bb0\u5f55\u6570\uff1a") or line.startswith("\u6267\u884c\u6210\u529f") or line.startswith("\u8017\u65f6\uff1a"):
            break
        if "\t" in line:
            parts = [part.strip() for part in line.split("\t")]
            while parts and parts[-1] == "":
                parts.pop()
            if parts:
                parts = parts[:1] + parts[1:5] + [""] * max(0, 5 - len(parts))
                rows.append(
                    {
                        "dnn": parts[0],
                        "n3Vrf": parts[1] if len(parts) > 1 else "",
                        "n6Vrf": parts[2] if len(parts) > 2 else "",
                        "n9Vrf": parts[3] if len(parts) > 3 else "",
                        "s1uVrf": parts[4] if len(parts) > 4 else "",
                    }
                )
                continue
        parts = re.split(r"\s{2,}", line)
        parts = [part.strip() for part in parts if part.strip()]
        if parts:
            parts = parts[:5] + [""] * max(0, 5 - len(parts))
            rows.append(
                {
                    "dnn": parts[0],
                    "n3Vrf": parts[1],
                    "n6Vrf": parts[2],
                    "n9Vrf": parts[3],
                    "s1uVrf": parts[4],
                }
            )
    return rows


def _normalize_web_dnn_rows(rows: list[dict], dnn_name: str | None = None) -> list[dict]:
    canonical_label = "\u0044\u004e\u004e\u540d\u79f0"
    field_aliases = {
        "dnn名称": canonical_label,
        "dnn": canonical_label,
        "n3 vrfid": "N3 VRFID",
        "n6 vrfid": "N6 VRFID",
        "n9 vrfid": "N9 VRFID",
        "s1u vrfid": "S1U VRFID",
    }
    normalized_rows = []
    for row in rows:
        normalized = {}
        for key, value in row.items():
            normalized[key] = _normalize_compare_value(value)
            key_norm = _normalize_compare_key(key)
            if key_norm in field_aliases:
                normalized[field_aliases[key_norm]] = _normalize_compare_value(value)
        if dnn_name and normalized.get(canonical_label, "") != dnn_name:
            continue
        normalized_rows.append(normalized)
    return normalized_rows


def _normalize_db_dnn_rows(rows: list[dict[str, Any]], dnn_name: str | None = None) -> tuple[list[dict], dict[str, dict]]:
    canonical_label = "\u0044\u004e\u004e\u540d\u79f0"
    normalized_rows = []
    normalized_map = {}
    for row in rows:
        normalized = {
            canonical_label: _normalize_compare_value(row.get("dnn")),
            "N3 VRFID": _normalize_compare_value(row.get("n3Vrf")),
            "N6 VRFID": _normalize_compare_value(row.get("n6Vrf")),
            "N9 VRFID": _normalize_compare_value(row.get("n9Vrf")),
            "S1U VRFID": _normalize_compare_value(row.get("s1uVrf")),
            "_db_source": row,
        }
        dnn_value = normalized[canonical_label]
        if dnn_name and dnn_value != dnn_name:
            continue
        normalized_rows.append(normalized)
        if dnn_value:
            normalized_map[dnn_value] = normalized
    return normalized_rows, normalized_map


def _build_dnn_compare_rows(web_rows: list[dict], db_row_map: dict[str, dict]) -> list[dict]:
    canonical_label = "\u0044\u004e\u004e\u540d\u79f0"
    compare_fields = [canonical_label, "N3 VRFID", "N6 VRFID", "N9 VRFID", "S1U VRFID"]
    results = []
    for web_row in web_rows:
        dnn_name = web_row.get(canonical_label, "")
        db_row = db_row_map.get(dnn_name)
        field_results = []
        match_count = 0
        for field in compare_fields:
            web_value = _normalize_compare_value(web_row.get(field, ""))
            db_value = _normalize_compare_value(db_row.get(field, "")) if db_row else ""
            status = "match" if db_row and web_value == db_value else "mismatch"
            if not db_row:
                status = "db_row_missing"
            if status == "match":
                match_count += 1
            field_results.append(
                {
                    "field": field,
                    "web_value": web_value,
                    "db_value": db_value,
                    "status": status,
                }
            )
        results.append(
            {
                "dnn_name": dnn_name,
                "status": "match" if match_count == len(compare_fields) else "mismatch",
                "web_row": {field: _normalize_compare_value(web_row.get(field, "")) for field in compare_fields},
                "db_row": {field: _normalize_compare_value(db_row.get(field, "")) for field in compare_fields} if db_row else {},
                "field_results": field_results,
            }
        )
    return results


def _parse_dnn_rows_from_tail_text(tail_text: str, dnn_name: str | None = None) -> list[dict]:
    lines = [" ".join(line.split()) for line in tail_text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return []

    rows = []
    for index, line in enumerate(lines):
        if dnn_name and line != dnn_name:
            continue
        if line in {"新增 修改 删除", "操作维护"}:
            continue
        if not re.fullmatch(r"[A-Za-z0-9._-]+(?:\.[A-Za-z0-9._-]+)*", line):
            continue

        numeric_values: list[str] = []
        for follow in lines[index + 1 :]:
            if re.fullmatch(r"\d+", follow):
                numeric_values.append(follow)
                if len(numeric_values) == 4:
                    break
            elif numeric_values:
                break
        if len(numeric_values) == 4:
            rows.append(
                {
                    "DNN名称": line,
                    "N3 VRFID": numeric_values[0],
                    "N6 VRFID": numeric_values[1],
                    "N9 VRFID": numeric_values[2],
                    "S1U VRFID": numeric_values[3],
                }
            )
    if dnn_name:
        rows = [row for row in rows if row.get("DNN名称") == dnn_name]
    return rows


def compare_dnn_web_with_db(page, task: dict, debug_dir: Path) -> dict:
    canonical_label = "\u0044\u004e\u004e\u540d\u79f0"
    merged_task = _default_dnn_compare_task(task)
    query_result = query_config_task(page, merged_task, debug_dir)

    db_config = merged_task.get("db")
    if not isinstance(db_config, dict):
        raise RuntimeError("task.db must be provided and must be an object.")

    dnn_name = merged_task.get("dnn_name")
    db_config = dict(db_config)
    raw_web_rows = query_result.get("rows", [])
    if not raw_web_rows:
        raw_web_rows = _parse_dnn_rows_from_tail_text(query_result.get("tail_text", ""), dnn_name)
    web_rows = _normalize_web_dnn_rows(raw_web_rows, dnn_name)
    ne_spec, ne_ip = _resolve_ne(merged_task)
    db_rows_raw = _load_db_dnn_rows(db_config, ne_ip)
    db_rows, db_row_map = _normalize_db_dnn_rows(db_rows_raw, dnn_name)
    comparisons = _build_dnn_compare_rows(web_rows, db_row_map)

    web_dnn_names = {row.get(canonical_label, "") for row in web_rows if row.get(canonical_label, "")}
    db_only_rows = [
        {key: value for key, value in row.items() if key != "_db_source"}
        for row in db_rows
        if row.get(canonical_label, "") not in web_dnn_names
    ]

    mismatch_row_count = sum(1 for item in comparisons if item["status"] != "match")
    return {
        "mode": "dnn_compare",
        "network_element": ne_spec.get("display_name", ne_ip),
        "network_element_ip": ne_ip,
        "path_by_name": merged_task["path_by_name"],
        "command_text": merged_task["command_text"],
        "dnn_name": dnn_name,
        "web_headers": query_result.get("headers", []),
        "web_rows": web_rows,
        "db_rows": [{key: value for key, value in row.items() if key != "_db_source"} for row in db_rows],
        "comparisons": comparisons,
        "db_only_rows": db_only_rows,
        "summary": {
            "web_row_count": len(web_rows),
            "db_row_count": len(db_rows),
            "compared_row_count": len(comparisons),
            "mismatch_row_count": mismatch_row_count,
            "all_match": mismatch_row_count == 0 and len(comparisons) > 0,
        },
        "debug": {
            "detected_fields": query_result.get("detected_fields", []),
            "tail_text": query_result.get("tail_text", ""),
        },
    }


def _load_db_vrf_rows(db_config: dict, ne_ip: str) -> list[dict[str, Any]]:
    source = str(db_config.get("source", "config_snapshot"))
    if source == "mml_exec_log_show":
        return _load_db_vrf_rows_from_mml_exec_log(db_config, ne_ip)

    schema = str(db_config.get("schema", "ems_upf"))
    ne_ip_escaped = ne_ip.replace("'", "''")
    sql = f"""
WITH target_ne AS (
  SELECT running_conf
  FROM {schema}.cm_ne
  WHERE ne_ip = '{ne_ip_escaped}'
    AND COALESCE(is_deleted, false) = false
  ORDER BY update_time DESC NULLS LAST
  LIMIT 1
)
SELECT COALESCE(((running_conf::jsonb)->'upf'->'upfVrfConf')::text, '[]')
FROM target_ne;
""".strip()
    output = run_psql_query(db_config, sql)
    if not output:
        return []
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Unable to parse VRF JSON from database: {exc}") from exc
    if not isinstance(parsed, list):
        raise RuntimeError("Database VRF data is not a JSON array.")
    return [item for item in parsed if isinstance(item, dict)]


def _load_db_vrf_rows_from_mml_exec_log(db_config: dict, ne_ip: str) -> list[dict[str, Any]]:
    schema = str(db_config.get("schema", "ems_upf"))
    ne_ip_escaped = ne_ip.replace("'", "''")
    vrf_id = str(db_config.get("vrf_id", "")).replace("'", "''")
    vrf_filter = f" AND mml_exec_command LIKE '%VRFID={vrf_id};%'" if vrf_id else ""
    sql = f"""
SELECT COALESCE(format_result, raw_result, '')
FROM {schema}.cm_mml_exec_log
WHERE success = true
  AND mml_exec_command LIKE 'SHOW UPFVRFCONF%'
  {vrf_filter}
ORDER BY insert_time DESC
LIMIT 1;
""".strip()
    output = run_psql_query(db_config, sql)
    if not output:
        return []
    rows = _parse_vrf_show_result_text(output)
    return [{"vrfId": row.get("VRF ID", "")} for row in rows]


def _default_vrf_compare_task(task: dict) -> dict:
    merged = dict(task)
    merged.setdefault("path_by_name", VRF_COMPARE_PATH)
    merged.setdefault("command_text", VRF_COMPARE_COMMAND)
    merged.setdefault("result_wait_ms", 12000)

    params = dict(merged.get("params", {}))
    vrf_id = merged.get("vrf_id")
    if vrf_id is not None and "VRF ID" not in params:
        params["VRF ID"] = vrf_id
    merged["params"] = params
    return merged


def _parse_vrf_show_result_text(result_text: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in result_text.splitlines() if line.strip()]
    header_index = -1
    for idx, line in enumerate(lines):
        parts = [part.strip() for part in re.split(r"\t+|\s{2,}", line) if part.strip()]
        if parts == ["VRF ID"] or "VRF ID" in parts:
            header_index = idx
            break
    if header_index == -1:
        return []

    rows = []
    for line in lines[header_index + 1 :]:
        if set(line) == {"-"}:
            continue
        if line.startswith("记录数：") or line.startswith("执行成功") or line.startswith("耗时："):
            break
        parts = [part.strip() for part in re.split(r"\t+|\s{2,}", line) if part.strip()]
        if parts:
            rows.append({"VRF ID": parts[0]})
    return rows


def _normalize_web_vrf_rows(rows: list[dict], vrf_id: str | None = None, result_text: str | None = None) -> list[dict]:
    source_rows = rows if rows else _parse_vrf_show_result_text(result_text or "")
    normalized_rows = []
    expected_vrf_id = _normalize_compare_value(vrf_id) if vrf_id is not None else None
    for row in source_rows:
        normalized = {"VRF ID": _normalize_compare_value(row.get("VRF ID", row.get("vrfId", "")))}
        if expected_vrf_id is not None and normalized["VRF ID"] != expected_vrf_id:
            continue
        normalized_rows.append(normalized)
    return normalized_rows


def _normalize_db_vrf_rows(rows: list[dict[str, Any]], vrf_id: str | None = None) -> tuple[list[dict], dict[str, dict]]:
    normalized_rows = []
    normalized_map = {}
    expected_vrf_id = _normalize_compare_value(vrf_id) if vrf_id is not None else None
    for row in rows:
        normalized = {
            "VRF ID": _normalize_compare_value(row.get("vrfId")),
            "_db_source": row,
        }
        current_vrf_id = normalized["VRF ID"]
        if expected_vrf_id is not None and current_vrf_id != expected_vrf_id:
            continue
        normalized_rows.append(normalized)
        if current_vrf_id:
            normalized_map[current_vrf_id] = normalized
    return normalized_rows, normalized_map


def _build_vrf_compare_rows(web_rows: list[dict], db_row_map: dict[str, dict]) -> list[dict]:
    results = []
    for web_row in web_rows:
        current_vrf_id = web_row.get("VRF ID", "")
        db_row = db_row_map.get(current_vrf_id)
        web_value = _normalize_compare_value(current_vrf_id)
        db_value = _normalize_compare_value(db_row.get("VRF ID", "")) if db_row else ""
        status = "match" if db_row and web_value == db_value else "mismatch"
        if not db_row:
            status = "db_row_missing"
        results.append(
            {
                "vrf_id": current_vrf_id,
                "status": status,
                "web_row": {"VRF ID": web_value},
                "db_row": {"VRF ID": db_value} if db_row else {},
                "field_results": [
                    {
                        "field": "VRF ID",
                        "web_value": web_value,
                        "db_value": db_value,
                        "status": status,
                    }
                ],
            }
        )
    return results


def compare_vrf_web_with_db(page, task: dict, debug_dir: Path) -> dict:
    merged_task = _default_vrf_compare_task(task)
    query_result = query_config_task(page, merged_task, debug_dir)

    db_config = merged_task.get("db")
    if not isinstance(db_config, dict):
        raise RuntimeError("task.db must be provided and must be an object.")

    vrf_id = merged_task.get("vrf_id")
    db_config = dict(db_config)
    if vrf_id is not None:
        db_config.setdefault("vrf_id", _normalize_compare_value(vrf_id))
    web_rows = _normalize_web_vrf_rows(
        query_result.get("rows", []),
        vrf_id,
        query_result.get("result_text", ""),
    )
    ne_spec, ne_ip = _resolve_ne(merged_task)
    db_rows_raw = _load_db_vrf_rows(db_config, ne_ip)
    db_rows, db_row_map = _normalize_db_vrf_rows(db_rows_raw, vrf_id)
    comparisons = _build_vrf_compare_rows(web_rows, db_row_map)

    web_vrf_ids = {row.get("VRF ID", "") for row in web_rows if row.get("VRF ID", "")}
    db_only_rows = [
        {key: value for key, value in row.items() if key != "_db_source"}
        for row in db_rows
        if row.get("VRF ID", "") not in web_vrf_ids
    ]

    mismatch_row_count = sum(1 for item in comparisons if item["status"] != "match")
    return {
        "mode": "vrf_compare",
        "network_element": ne_spec.get("display_name", ne_ip),
        "network_element_ip": ne_ip,
        "path_by_name": merged_task["path_by_name"],
        "command_text": merged_task["command_text"],
        "vrf_id": _normalize_compare_value(vrf_id) if vrf_id is not None else None,
        "web_headers": query_result.get("headers", []),
        "web_rows": web_rows,
        "db_rows": [{key: value for key, value in row.items() if key != "_db_source"} for row in db_rows],
        "comparisons": comparisons,
        "db_only_rows": db_only_rows,
        "summary": {
            "web_row_count": len(web_rows),
            "db_row_count": len(db_rows),
            "compared_row_count": len(comparisons),
            "mismatch_row_count": mismatch_row_count,
            "all_match": mismatch_row_count == 0 and len(comparisons) > 0,
        },
        "debug": {
            "detected_fields": query_result.get("detected_fields", []),
            "tail_text": query_result.get("tail_text", ""),
            "result_text": query_result.get("result_text", ""),
        },
    }


def _json_expr_from_upf_array_key(array_key: str) -> str:
    safe_key = str(array_key).replace("'", "''")
    return f"(running_conf::jsonb)->'upf'->'{safe_key}'"


def _load_db_generic_rows(db_config: dict, ne_ip: str) -> list[dict[str, Any]]:
    source = str(db_config.get("source", "config_snapshot"))
    if source != "config_snapshot":
        raise RuntimeError(f"Unsupported generic db source: {source}")

    array_key = db_config.get("array_key")
    if not array_key:
        raise RuntimeError("task.db.array_key is required for config-generic-compare.")

    schema = str(db_config.get("schema", "ems_upf"))
    ne_ip_escaped = ne_ip.replace("'", "''")
    json_expr = _json_expr_from_upf_array_key(str(array_key))
    sql = f"""
WITH target_ne AS (
  SELECT running_conf
  FROM {schema}.cm_ne
  WHERE ne_ip = '{ne_ip_escaped}'
    AND COALESCE(is_deleted, false) = false
  ORDER BY update_time DESC NULLS LAST
  LIMIT 1
)
SELECT COALESCE(({json_expr})::text, '[]')
FROM target_ne;
""".strip()
    output = run_psql_query(db_config, sql)
    if not output:
        return []
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Unable to parse generic JSON from database: {exc}") from exc
    if isinstance(parsed, dict):
        return [parsed]
    if not isinstance(parsed, list):
        raise RuntimeError("Generic database data is not a JSON array or object.")
    return [item for item in parsed if isinstance(item, dict)]


def _build_expected_web_key(task: dict, key_fields: list[str]) -> dict[str, str]:
    params = task.get("params", {})
    expected = {}
    for field in key_fields:
        if field in params:
            expected[field] = _normalize_compare_value(params[field])
    return expected


def _normalize_web_generic_rows(
    rows: list[dict],
    web_to_db_field_map: dict[str, str],
    compare_fields: list[str],
    expected_key: dict[str, str],
) -> list[dict]:
    normalized_rows = []
    for row in rows:
        normalized = {}
        for web_field in compare_fields:
            normalized[web_field] = _normalize_compare_value(row.get(web_field, ""))
        if expected_key and any(normalized.get(field, "") != value for field, value in expected_key.items()):
            continue
        normalized_rows.append(normalized)
    return normalized_rows


def _normalize_db_generic_rows(
    rows: list[dict[str, Any]],
    web_to_db_field_map: dict[str, str],
    compare_fields: list[str],
    expected_key: dict[str, str],
) -> tuple[list[dict], dict[tuple[str, ...], dict]]:
    normalized_rows = []
    normalized_map: dict[tuple[str, ...], dict] = {}
    key_fields = list(expected_key.keys())
    for row in rows:
        normalized = {
            web_field: _normalize_compare_value(row.get(db_field))
            for web_field, db_field in web_to_db_field_map.items()
            if web_field in compare_fields or web_field in key_fields
        }
        if expected_key and any(normalized.get(field, "") != value for field, value in expected_key.items()):
            continue
        normalized["_db_source"] = row
        normalized_rows.append(normalized)
        row_key = tuple(normalized.get(field, "") for field in key_fields) if key_fields else ()
        if row_key:
            normalized_map[row_key] = normalized
    return normalized_rows, normalized_map


def _build_generic_compare_rows(
    web_rows: list[dict],
    db_row_map: dict[tuple[str, ...], dict],
    key_fields: list[str],
    compare_fields: list[str],
) -> list[dict]:
    results = []
    for web_row in web_rows:
        row_key = tuple(web_row.get(field, "") for field in key_fields)
        db_row = db_row_map.get(row_key)
        field_results = []
        match_count = 0
        for field in compare_fields:
            web_value = _normalize_compare_value(web_row.get(field, ""))
            db_value = _normalize_compare_value(db_row.get(field, "")) if db_row else ""
            status = "match" if db_row and web_value == db_value else "mismatch"
            if not db_row:
                status = "db_row_missing"
            if status == "match":
                match_count += 1
            field_results.append(
                {
                    "field": field,
                    "web_value": web_value,
                    "db_value": db_value,
                    "status": status,
                }
            )
        results.append(
            {
                "row_key": {field: web_row.get(field, "") for field in key_fields},
                "status": "match" if match_count == len(compare_fields) else "mismatch",
                "web_row": {field: _normalize_compare_value(web_row.get(field, "")) for field in compare_fields},
                "db_row": {field: _normalize_compare_value(db_row.get(field, "")) for field in compare_fields} if db_row else {},
                "field_results": field_results,
            }
        )
    return results


def compare_generic_config_web_with_db(page, task: dict, debug_dir: Path) -> dict:
    compare_spec = task.get("compare")
    if not isinstance(compare_spec, dict):
        raise RuntimeError("task.compare must be provided and must be an object.")

    web_to_db_field_map = compare_spec.get("web_to_db_field_map")
    if not isinstance(web_to_db_field_map, dict) or not web_to_db_field_map:
        raise RuntimeError("task.compare.web_to_db_field_map must be a non-empty object.")

    compare_fields = compare_spec.get("compare_fields") or list(web_to_db_field_map.keys())
    key_fields = compare_spec.get("key_fields") or [compare_fields[0]]
    if not isinstance(compare_fields, list) or not all(isinstance(item, str) for item in compare_fields):
        raise RuntimeError("task.compare.compare_fields must be a string list.")
    if not isinstance(key_fields, list) or not all(isinstance(item, str) for item in key_fields):
        raise RuntimeError("task.compare.key_fields must be a string list.")

    query_result = query_config_task(page, task, debug_dir)

    db_config = task.get("db")
    if not isinstance(db_config, dict):
        raise RuntimeError("task.db must be provided and must be an object.")

    expected_key = _build_expected_web_key(task, key_fields)
    web_rows = _normalize_web_generic_rows(query_result.get("rows", []), web_to_db_field_map, compare_fields, expected_key)
    ne_spec, ne_ip = _resolve_ne(task)
    db_rows_raw = _load_db_generic_rows(db_config, ne_ip)
    db_rows, db_row_map = _normalize_db_generic_rows(db_rows_raw, web_to_db_field_map, compare_fields, expected_key)
    comparisons = _build_generic_compare_rows(web_rows, db_row_map, key_fields, compare_fields)

    web_row_keys = {tuple(row.get(field, "") for field in key_fields) for row in web_rows}
    db_only_rows = [
        {key: value for key, value in row.items() if key != "_db_source"}
        for row in db_rows
        if tuple(row.get(field, "") for field in key_fields) not in web_row_keys
    ]

    mismatch_row_count = sum(1 for item in comparisons if item["status"] != "match")
    return {
        "mode": "generic_compare",
        "network_element": ne_spec.get("display_name", ne_ip),
        "network_element_ip": ne_ip,
        "path_by_name": task["path_by_name"],
        "command_text": task.get("command_text"),
        "key_fields": key_fields,
        "compare_fields": compare_fields,
        "web_rows": web_rows,
        "db_rows": [{key: value for key, value in row.items() if key != "_db_source"} for row in db_rows],
        "comparisons": comparisons,
        "db_only_rows": db_only_rows,
        "summary": {
            "web_row_count": len(web_rows),
            "db_row_count": len(db_rows),
            "compared_row_count": len(comparisons),
            "mismatch_row_count": mismatch_row_count,
            "all_match": mismatch_row_count == 0 and len(comparisons) > 0,
        },
        "debug": {
            "detected_fields": query_result.get("detected_fields", []),
            "tail_text": query_result.get("tail_text", ""),
            "result_text": query_result.get("result_text", ""),
        },
    }
