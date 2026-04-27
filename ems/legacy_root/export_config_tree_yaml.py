# -*- coding: utf-8 -*-
from pathlib import Path
from playwright.sync_api import sync_playwright
import yaml

COOKIE_STRING = r"""grafana_session=375ebbf8a684e2edf91eb31fbcca6345; grafana_session_expiry=1772268912; rememberMe=true; username=SXlpr7Diy6dbcTLKq2oWWgEBX8VAJIXdf6S/cWmQZX5adfOrNImsCHSzIOO8lgxYLLV8nWUDM5vNLx3g9HR9WiPSyR938o0XkfaD5/M8q7tJpRrIcamvBhIIxbOIHz/L/rXUdt12CLS+4RNZ+f17hFIoI2wV3LZJXQcBRoxTwwUnBAvn8CnGZpld3HidNucYijtKv/XmJvRn1aiE6FQE75CB2GevNYimqlFgrfvh7Bmuuh9Pii4CcG/z60r8JS3WQ+DrMIXSHMtVqHoc/cKErpXvXqqQizC6xA==; password=ZHuwup+Q6oOBz9PBqXZKXXD8qLa1ej4S67D3nPkqMzpfxsdOXyQ5NtTdBcZwQp/psa8E6ju81ICrtiNEezQkgWDyycNA23fbqHCgW350cEFUryWLAIxmE6M9EfTGe6Lgm4mStaIgBtV2wtEyL7m1otdTIRzukgfFQ7FlhzldA+e1m66MYMRaYq6T/Xkgepw5eijVYHpwJCnZwVc3ToLmJz0dQZfuqB/gUOTvwGt3U3vXgAAPo9rMQVxsfx9bmc2SsSnQvHm9URzCbd9OGcmH8XnLV4eMBh2AKbD/sXuAyR9NffRK0G0xRtu/DBIubZR/jcpPEN1eH89kN0iSldZ0WQ==; JSESSIONID=52ed6a64-1238-438b-896e-94a25829fcfc; HFLAG=true; oldPassword=OQo46BCqTdOfd8hn9yWlrR/djnKo4gJpWFu+6p5kCGXoNn1U2aG37eSb2qImNx+I7Kq3/+shdL92LgS5P5DxXqi3RkmDpJO7pqJ/cFCfcyRivAJjbo5gpEc72410ys0Ub9SLloJUO4DUIqdaQ1tU0W3rKoIay8sotsy6c0CFfJE3eUcKIYSVqj3iH4SHhDDoJP3Z1WIvxji/ms/p53PlubbbashZ1vH3VmAiex63QbhsBuMam2am9/fJgtUMvpE4bBHo+YYkfRRxu7PLfRB2dK0zoNfOoLrHa5ppUJr3DTtjiEb6fXFwQebvOTTBnuxCTWJB7qvtbjn1x6rMrXK64A=="""

BASE_URL = "https://127.0.0.1:50443"
TARGET_URL = BASE_URL + "/index#/topoOverview"
NE_IP = "10.230.4.248"
OUTPUT_YAML = "config_tree.yaml"
DEBUG_DIR = Path("ems_debug")
DEBUG_DIR.mkdir(exist_ok=True)


def build_cookies(cookie_string: str):
    cookies = []
    for part in cookie_string.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies.append({
            "name": name.strip(),
            "value": value.strip(),
            "domain": "127.0.0.1",
            "path": "/",
            "secure": True,
            "httpOnly": False,
            "sameSite": "Lax",
        })
    return cookies


def wait(page, ms=1000):
    page.wait_for_timeout(ms)


def dump_debug(page, prefix):
    try:
        (DEBUG_DIR / f"{prefix}.txt").write_text(page.locator("body").inner_text(timeout=5000), encoding="utf-8")
    except Exception:
        pass
    try:
        (DEBUG_DIR / f"{prefix}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    try:
        page.screenshot(path=str(DEBUG_DIR / f"{prefix}.png"), full_page=True)
    except Exception:
        pass


def click_top_menu(page, name):
    menu = page.locator(".el-submenu__title").filter(has_text=name).first
    menu.wait_for(state="visible", timeout=20000)
    menu.hover()
    wait(page, 1000)


def click_popup_item(page, name):
    item = page.locator(".el-menu--popup .el-menu-item").filter(has_text=name).first
    item.wait_for(state="visible", timeout=20000)
    item.click()
    wait(page, 2500)


def choose_network_element(page, ne_ip):
    page.locator(".el-select:visible").first.click()
    wait(page, 1000)

    pop = page.locator(".el-select-dropdown:visible, .el-popper:visible").filter(has_text=ne_ip).first
    pop.wait_for(state="visible", timeout=20000)

    candidates = [
        pop.locator(".el-tree-node__content:visible").filter(has_text=ne_ip),
        pop.locator(".custom-tree-icon:visible").filter(has_text=ne_ip),
        pop.locator(".el-tree-node:visible").filter(has_text=ne_ip),
        pop.locator(".el-select-dropdown__item:visible").filter(has_text=ne_ip),
    ]

    for loc in candidates:
        if loc.count() > 0:
            loc.first.click()
            wait(page, 2500)
            return

    raise RuntimeError(f"未找到网元选项: {ne_ip}")


def expand_all_tree_nodes(page, rounds=6):
    js_scan_and_expand = """
    () => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 &&
               s.display !== 'none' &&
               s.visibility !== 'hidden';
      }

      const tree = document.querySelector('.el-tree');
      if (!tree) return { expanded: 0, names: [] };

      let expanded = 0;
      const names = [];

      const nodes = Array.from(tree.querySelectorAll('.el-tree-node.is-focusable'));
      for (const node of nodes) {
        if (!visible(node)) continue;
        if (node.classList.contains('is-expanded')) continue;

        const content = node.querySelector(':scope > .el-tree-node__content');
        if (!content || !visible(content)) continue;

        const icon = content.querySelector('i');
        const iconCls = icon ? icon.className : '';
        if (!iconCls.includes('icon-folder')) continue;

        const text = (content.innerText || content.textContent || '').trim().replace(/\\s+/g, ' ');
        if (!text) continue;

        content.scrollIntoView({ block: 'center' });
        content.click();
        expanded += 1;
        names.push(text);
      }

      return { expanded, names };
    }
    """

    js_scroll_info = """
    () => {
      const wrap =
        document.querySelector('.el-aside .el-scrollbar__wrap') ||
        document.querySelector('.mml-aside .el-scrollbar__wrap') ||
        document.querySelector('.el-aside') ||
        document.querySelector('.mml-aside');

      if (!wrap) return null;

      return {
        top: wrap.scrollTop,
        height: wrap.clientHeight,
        scrollHeight: wrap.scrollHeight
      };
    }
    """

    js_scroll_down = """
    () => {
      const wrap =
        document.querySelector('.el-aside .el-scrollbar__wrap') ||
        document.querySelector('.mml-aside .el-scrollbar__wrap') ||
        document.querySelector('.el-aside') ||
        document.querySelector('.mml-aside');

      if (!wrap) return false;
      wrap.scrollTop = wrap.scrollTop + wrap.clientHeight - 40;
      return true;
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

    for _ in range(rounds):
        page.evaluate(js_scroll_top)
        wait(page, 800)

        last_top = -1
        while True:
            result = page.evaluate(js_scan_and_expand)
            if result["expanded"] > 0:
                wait(page, 1200)

            info = page.evaluate(js_scroll_info)
            if not info:
                break

            if info["top"] == last_top and info["top"] + info["height"] >= info["scrollHeight"] - 5:
                break

            last_top = info["top"]
            page.evaluate(js_scroll_down)
            wait(page, 800)

        page.evaluate(js_scroll_top)
        wait(page, 800)


def extract_tree_dom(page):
    js = """
    () => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 &&
               s.display !== 'none' &&
               s.visibility !== 'hidden';
      }

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

        const icon = content.querySelector('i');
        const iconCls = icon ? icon.className : '';

        const currentPath = [...path, text];
        const item = {
          name: text,
          expanded: node.classList.contains('is-expanded'),
          current: node.classList.contains('is-current'),
          icon_class: iconCls,
          path_by_name: currentPath,
          children: [],
        };

        if (text.includes('(') && text.includes(')')) {
          const start = text.lastIndexOf('(');
          const end = text.lastIndexOf(')');
          if (start !== -1 && end !== -1 && end > start) {
            item.command = text.slice(start + 1, end).trim();
            item.execute_method = {
              type: 'command_processing',
              network_element: '%NE_IP%',
              path_by_name: currentPath
            };
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
    return page.evaluate(js.replace("%NE_IP%", NE_IP))


def normalize_tree(items, level=0):
    out = []
    for item in items:
        node = {
            "name": item["name"],
            "level": level,
            "expanded": item.get("expanded", False),
        }
        if "command" in item:
            node["command"] = item["command"]
            node["execute_method"] = item["execute_method"]
        node["children"] = normalize_tree(item.get("children", []), level + 1)
        out.append(node)
    return out


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1600, "height": 1000},
        )
        context.add_cookies(build_cookies(COOKIE_STRING))
        page = context.new_page()

        page.goto(TARGET_URL, wait_until="commit", timeout=120000)
        wait(page, 8000)
        dump_debug(page, "01_topo")

        click_top_menu(page, "配置")
        click_popup_item(page, "命令处理")
        wait(page, 5000)
        dump_debug(page, "02_command_processing")

        choose_network_element(page, NE_IP)
        wait(page, 5000)
        dump_debug(page, "03_ne_selected")

        expand_all_tree_nodes(page, rounds=8)
        wait(page, 1500)
        dump_debug(page, "04_tree_expanded")

        raw_tree = extract_tree_dom(page)
        browser.close()

    result = {
        "site": BASE_URL,
        "entry": TARGET_URL,
        "module": "配置 -> 命令处理",
        "network_element": NE_IP,
        "tree": normalize_tree(raw_tree, 0),
    }

    with open(OUTPUT_YAML, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            result,
            f,
            allow_unicode=True,
            sort_keys=False,
            width=200
        )

    print(f"YAML 已输出到: {OUTPUT_YAML}")


if __name__ == "__main__":
    main()
