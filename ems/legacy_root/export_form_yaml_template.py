# -*- coding: utf-8 -*-
from pathlib import Path

from playwright.sync_api import sync_playwright
import yaml


COOKIE_STRING = r"""grafana_session=375ebbf8a684e2edf91eb31fbcca6345; grafana_session_expiry=1772268912; rememberMe=true; username=SXlpr7Diy6dbcTLKq2oWWgEBX8VAJIXdf6S/cWmQZX5adfOrNImsCHSzIOO8lgxYLLV8nWUDM5vNLx3g9HR9WiPSyR938o0XkfaD5/M8q7tJpRrIcamvBhIIxbOIHz/L/rXUdt12CLS+4RNZ+f17hFIoI2wV3LZJXQcBRoxTwwUnBAvn8CnGZpld3HidNucYijtKv/XmJvRn1aiE6FQE75CB2GevNYimqlFgrfvh7Bmuuh9Pii4CcG/z60r8JS3WQ+DrMIXSHMtVqHoc/cKErpXvXqqQizC6xA==; password=ZHuwup+Q6oOBz9PBqXZKXXD8qLa1ej4S67D3nPkqMzpfxsdOXyQ5NtTdBcZwQp/psa8E6ju81ICrtiNEezQkgWDyycNA23fbqHCgW350cEFUryWLAIxmE6M9EfTGe6Lgm4mStaIgBtV2wtEyL7m1otdTIRzukgfFQ7FlhzldA+e1m66MYMRaYq6T/Xkgepw5eijVYHpwJCnZwVc3ToLmJz0dQZfuqB/gUOTvwGt3U3vXgAAPo9rMQVxsfx9bmc2SsSnQvHm9URzCbd9OGcmH8XnLV4eMBh2AKbD/sXuAyR9NffRK0G0xRtu/DBIubZR/jcpPEN1eH89kN0iSldZ0WQ==; JSESSIONID=52ed6a64-1238-438b-896e-94a25829fcfc; HFLAG=true; oldPassword=OQo46BCqTdOfd8hn9yWlrR/djnKo4gJpWFu+6p5kCGXoNn1U2aG37eSb2qImNx+I7Kq3/+shdL92LgS5P5DxXqi3RkmDpJO7pqJ/cFCfcyRivAJjbo5gpEc72410ys0Ub9SLloJUO4DUIqdaQ1tU0W3rKoIay8sotsy6c0CFfJE3eUcKIYSVqj3iH4SHhDDoJP3Z1WIvxji/ms/p53PlubbbashZ1vH3VmAiex63QbhsBuMam2am9/fJgtUMvpE4bBHo+YYkfRRxu7PLfRB2dK0zoNfOoLrHa5ppUJr3DTtjiEb6fXFwQebvOTTBnuxCTWJB7qvtbjn1x6rMrXK64A=="""

BASE_URL = "https://127.0.0.1:50443"
TARGET_URL = BASE_URL + "/index#/topoOverview"
NE_IP = "10.230.4.248"
OUTPUT_YAML = "config_tree_with_fields.yaml"
DEBUG_DIR = Path("ems_debug")
DEBUG_DIR.mkdir(exist_ok=True)

ALLOWED_COMMAND_PREFIXES = ("SHOW ", "ADD ", "SET ", "DEL ")


def build_cookies(cookie_string: str):
    cookies = []
    for part in cookie_string.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies.append(
            {
                "name": name.strip(),
                "value": value.strip(),
                "domain": "127.0.0.1",
                "path": "/",
                "secure": True,
                "httpOnly": False,
                "sameSite": "Lax",
            }
        )
    return cookies


def wait(page, ms=800):
    page.wait_for_timeout(ms)


def dump_debug(page, prefix):
    try:
        (DEBUG_DIR / f"{prefix}.txt").write_text(
            page.locator("body").inner_text(timeout=3000), encoding="utf-8"
        )
    except Exception:
        pass
    try:
        page.screenshot(path=str(DEBUG_DIR / f"{prefix}.png"), full_page=True)
    except Exception:
        pass


def wait_text(page, text, timeout=15000):
    page.locator("body").filter(has_text=text).first.wait_for(
        state="visible", timeout=timeout
    )


def click_top_menu(page, name):
    menu = page.locator(".el-submenu__title").filter(has_text=name).first
    menu.wait_for(state="visible", timeout=15000)
    menu.hover()
    wait(page, 600)


def click_popup_item(page, name):
    item = page.locator(".el-menu--popup .el-menu-item").filter(has_text=name).first
    item.wait_for(state="visible", timeout=15000)
    item.click()
    wait(page, 1200)


def choose_network_element(page, ne_ip):
    page.locator(".el-select:visible").first.click()
    wait(page, 600)

    pop = (
        page.locator(".el-select-dropdown:visible, .el-popper:visible")
        .filter(has_text=ne_ip)
        .first
    )
    pop.wait_for(state="visible", timeout=15000)

    candidates = [
        pop.locator(".el-tree-node__content:visible").filter(has_text=ne_ip),
        pop.locator(".custom-tree-icon:visible").filter(has_text=ne_ip),
        pop.locator(".el-tree-node:visible").filter(has_text=ne_ip),
        pop.locator(".el-select-dropdown__item:visible").filter(has_text=ne_ip),
    ]

    for loc in candidates:
        if loc.count() > 0:
            loc.first.click()
            wait(page, 1200)
            return

    raise RuntimeError(f"未找到网元选项: {ne_ip}")


def open_command_processing(page):
    try:
        body_text = page.locator("body").inner_text(timeout=2000)
        if "命令处理" in body_text and "选择网元" in body_text:
            return
    except Exception:
        pass

    click_top_menu(page, "配置")
    click_popup_item(page, "命令处理")
    wait_text(page, "选择网元", 15000)
    choose_network_element(page, NE_IP)
    wait_text(page, f"操作网元：{NE_IP}", 15000)


def click_tree_text(page, text, exact=False):
    candidates = [
        page.locator(".el-tree-node__content:visible"),
        page.locator(".custom-label:visible"),
        page.locator(".el-tooltip:visible"),
        page.locator(".el-tree-node:visible"),
        page.locator("div:visible"),
    ]

    matched = []
    for base in candidates:
        count = min(base.count(), 400)
        for i in range(count):
            loc = base.nth(i)
            try:
                raw = loc.inner_text(timeout=300)
            except Exception:
                continue

            normalized = " ".join(raw.split())
            if not normalized:
                continue

            ok = normalized == text if exact else text in normalized
            if ok:
                matched.append((len(normalized), loc))

    matched.sort(key=lambda x: x[0])

    for _, loc in matched[:8]:
        try:
            loc.scroll_into_view_if_needed()
            loc.click()
            wait(page, 800)
            return True
        except Exception:
            continue
    return False


def click_tree_path(page, path_by_name):
    js = """
    ({ path, expand_only }) => {
      function textOfContent(content) {
        const label =
          content.querySelector('.custom-label') ||
          content.querySelector('.el-tooltip') ||
          content;
        return (label.innerText || label.textContent || '').trim().replace(/\\s+/g, ' ');
      }

      function directChildNodes(container) {
        return Array.from(container.children).filter(el => el.classList.contains('el-tree-node'));
      }

      const tree = document.querySelector('.el-tree');
      if (!tree) return { ok: false, missing: '(tree)' };

      let container = tree;
      let found = null;

      for (const name of path) {
        found = null;
        for (const node of directChildNodes(container)) {
          const content = node.querySelector(':scope > .el-tree-node__content');
          if (!content) continue;
          const text = textOfContent(content);
          if (text === name || text.includes(name)) {
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

      if (expand_only) {
        if (!found.classList.contains('is-expanded')) {
          content.click();
          return { ok: true, action: 'expanded' };
        }
        return { ok: true, action: 'already-expanded' };
      }

      content.click();
      return { ok: true, action: 'clicked' };
    }
    """

    for idx in range(len(path_by_name)):
        path_prefix = path_by_name[: idx + 1]
        is_last = idx == len(path_by_name) - 1
        result = page.evaluate(js, {"path": path_prefix, "expand_only": not is_last})
        if not result["ok"]:
            raise RuntimeError(f"无法点击树节点: {result['missing']}")
        wait(page, 1000 if is_last else 700)


def soft_recover(page):
    try:
        page.goto(TARGET_URL, wait_until="commit", timeout=30000)
        wait(page, 3000)
        open_command_processing(page)
        wait(page, 1500)
        return True
    except Exception:
        return False


def expand_all_tree_nodes(page, rounds=6):
    js_expand = """
    () => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 &&
               s.display !== 'none' &&
               s.visibility !== 'hidden';
      }

      const tree = document.querySelector('.el-tree');
      if (!tree) return { expanded: 0 };

      let expanded = 0;
      const nodes = Array.from(tree.querySelectorAll('.el-tree-node.is-focusable'));

      for (const node of nodes) {
        if (!visible(node)) continue;
        if (node.classList.contains('is-expanded')) continue;

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


def extract_tree_dom(page):
    js = """
    () => {
      function textOfContent(content) {
        const label =
          content.querySelector('.custom-label') ||
          content.querySelector('.el-tooltip') ||
          content;
        return (label.innerText || label.textContent || '').trim().replace(/\\s+/g, ' ');
      }

      function parseNode(node, path) {
        const content = node.querySelector(':scope > .el-tree-node__content');
        if (!content) return null;

        const text = textOfContent(content);
        if (!text) return null;

        const currentPath = [...path, text];
        const item = {
          name: text,
          path_by_name: currentPath,
          children: [],
        };

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


def extract_form_fields(page):
    js = """
    () => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 &&
               s.display !== 'none' &&
               s.visibility !== 'hidden';
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


def filter_allowed_leaves(items, acc=None):
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


def attach_fields(items, field_map):
    for item in items:
        key = " > ".join(item["path_by_name"])
        if key in field_map:
            item["fields"] = field_map[key]
        attach_fields(item.get("children", []), field_map)


def normalize_tree(items, level=0):
    out = []
    for item in items:
        node = {
            "name": item["name"],
            "level": level,
            "children": normalize_tree(item.get("children", []), level + 1),
        }
        if "command" in item:
            node["command"] = item["command"]
            node["execute_method"] = {
                "type": "command_processing",
                "network_element": NE_IP,
                "path_by_name": item["path_by_name"],
            }
        if "fields" in item:
            node["fields"] = item["fields"]
        out.append(node)
    return out


def main():
    field_map = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1600, "height": 1000},
        )
        context.add_cookies(build_cookies(COOKIE_STRING))

        page = context.new_page()
        page.goto(TARGET_URL, wait_until="commit", timeout=120000)
        wait(page, 4000)

        open_command_processing(page)
        expand_all_tree_nodes(page, rounds=8)
        raw_tree = extract_tree_dom(page)
        dump_debug(page, "01_tree_ready")

        leaf_paths = filter_allowed_leaves(raw_tree)
        print(f"total allowed leaf paths: {len(leaf_paths)}")

        for idx, path_by_name in enumerate(leaf_paths, 1):
            path_text = " > ".join(path_by_name)
            print(f"[{idx}/{len(leaf_paths)}] processing: {path_text}")

            try:
                click_tree_path(page, path_by_name)
                wait(page, 1200)
                field_map[path_text] = extract_form_fields(page)
            except Exception as e:
                print(f"[{idx}/{len(leaf_paths)}] failed direct open: {path_text} -> {e}")

                if soft_recover(page):
                    try:
                        click_tree_path(page, path_by_name)
                        wait(page, 1200)
                        field_map[path_text] = extract_form_fields(page)
                    except Exception as e2:
                        print(
                            f"[{idx}/{len(leaf_paths)}] failed after recover: "
                            f"{path_text} -> {e2}"
                        )
                        field_map[path_text] = []
                        dump_debug(page, f"leaf_{idx:03d}_error")
                else:
                    field_map[path_text] = []
                    dump_debug(page, f"leaf_{idx:03d}_error")

        page.close()
        browser.close()

    attach_fields(raw_tree, field_map)

    result = {
        "site": BASE_URL,
        "entry": TARGET_URL,
        "module": "配置 -> 命令处理",
        "network_element": NE_IP,
        "tree": normalize_tree(raw_tree, 0),
    }

    with open(OUTPUT_YAML, "w", encoding="utf-8") as f:
        yaml.safe_dump(result, f, allow_unicode=True, sort_keys=False, width=200)

    print(f"YAML 已输出到: {OUTPUT_YAML}")


if __name__ == "__main__":
    main()
