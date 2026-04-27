# -*- coding: utf-8 -*-
from playwright.sync_api import sync_playwright
import json

COOKIE_STRING = r"""grafana_session=375ebbf8a684e2edf91eb31fbcca6345; grafana_session_expiry=1772268912; rememberMe=true; username=SXlpr7Diy6dbcTLKq2oWWgEBX8VAJ5vKTRKFn8Emi+dU5CDVy67jNAOjuE+eykeUtlMWBAU1EEIf0IyXAIXdf6S/cWmQZX5adfOrNImsCHSzIOO8lgxYLLV8nWUDM5vNLx3g9HR9WiPSyR938o0XkfaD5/M8q7tJpRrIcamvBhIIxbOIHz/L/rXUdt12CLS+4RNZ+f17hFIoI2wV3LZJXQcBRoxTwwUnBAvn8CnGZpld3HidNucYijtKv/XmJvRn1aiE6FQE75CB2GevNYimqlFgrfvh7Bmuuh9Pii4CcG/z60r8JS3WQ+DrMIXSHMtVqHoc/cKErpXvXqqQizC6xA==; password=ZHuwup+Q6oOBz9PBqXZKXXD8qLa1ej4S67D3nPkqMzpfxsdOXyQ5NtTdBcZwQp/psa8E6ju81ICrtiNEezQkgWDyycNA23fbqHCgW350cEFUryWLAIxmE6M9EfTGe6Lgm4mStaIgBtV2wtEyL7m1otdTIRzukgfFQ7FlhzldA+e1m66MYMRaYq6T/Xkgepw5eijVYHpwJCnZwVc3ToLmJz0dQZfuqB/gUOTvwGt3U3vXgAAPo9rMQVxsfx9bmc2SsSnQvHm9URzCbd9OGcmH8XnLV4eMBh2AKbD/sXuAyR9NffRK0G0xRtu/DBIubZR/jcpPEN1eH89kN0iSldZ0WQ==; JSESSIONID=52ed6a64-1238-438b-896e-94a25829fcfc; HFLAG=true; oldPassword=OQo46BCqTdOfd8hn9yWlrR/djnKo4gJpWFu+6p5kCGXoNn1U2aG37eSb2qImNx+I7Kq3/+shdL92LgS5P5DxXqi3RkmDpJO7pqJ/cFCfcyRivAJjbo5gpEc72410ys0Ub9SLloJUO4DUIqdaQ1tU0W3rKoIay8sotsy6c0CFfJE3eUcKIYSVqj3iH4SHhDDoJP3Z1WIvxji/ms/p53PlubbbashZ1vH3VmAiex63QbhsBuMam2am9/fJgtUMvpE4bBHo+YYkfRRxu7PLfRB2dK0zoNfOoLrHa5ppUJr3DTtjiEb6fXFwQebvOTTBnuxCTWJB7qvtbjn1x6rMrXK64A=="""

BASE_URL = "https://127.0.0.1:50443"
TARGET_URL = BASE_URL + "/index#/topoOverview"

# 这里改成你要执行的命令
TASK = {
    "network_element": "10.230.4.248",
    "path_by_name": [
        "业务开通配置",
        "VRF配置",
        "增加VRF配置"
    ],
    "params": {
        # 按页面字段名填写
        # 下面只是示例，请改成实际字段名和实际值
        "VRF ID": "777",
    }
}


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
        count = min(base.count(), 500)
        for i in range(count):
            loc = base.nth(i)
            try:
                raw = loc.inner_text(timeout=500)
            except Exception:
                continue

            normalized = " ".join(raw.split())
            if not normalized:
                continue

            ok = (normalized == text) if exact else (text in normalized)
            if ok:
                matched.append((len(normalized), loc, normalized))

    matched.sort(key=lambda x: x[0])

    for _, loc, _ in matched[:10]:
        try:
            loc.scroll_into_view_if_needed()
            loc.click()
            wait(page, 1200)
            return True
        except Exception:
            continue

    raise RuntimeError(f"未找到树节点: {text}")


def click_tree_path(page, path_by_name):
    for name in path_by_name:
        click_tree_text(page, name, exact=False)
        wait(page, 1500)


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

      for (const item of formItems) {
        if (!visible(item)) continue;

        const labelEl = item.querySelector('.el-form-item__label');
        const label = labelEl ? (labelEl.innerText || labelEl.textContent || '').trim().replace(/\\s+/g, ' ') : '';

        const input = item.querySelector('input, textarea');
        const select = item.querySelector('.el-select');
        const radios = Array.from(item.querySelectorAll('.el-radio'));
        const checks = Array.from(item.querySelectorAll('.el-checkbox'));

        if (input && visible(input)) {
          result.push({
            label,
            field_type: input.tagName.toLowerCase(),
            input_type: input.getAttribute('type') || '',
            placeholder: input.getAttribute('placeholder') || '',
            value: input.value || ''
          });
          continue;
        }

        if (select && visible(select)) {
          const inner = select.querySelector('input');
          result.push({
            label,
            field_type: 'select',
            input_type: '',
            placeholder: inner ? (inner.getAttribute('placeholder') || '') : '',
            value: inner ? (inner.value || '') : ''
          });
          continue;
        }

        if (radios.length > 0) {
          result.push({
            label,
            field_type: 'radio_group',
            input_type: '',
            placeholder: '',
            options: radios
              .map(r => (r.innerText || r.textContent || '').trim().replace(/\\s+/g, ' '))
              .filter(Boolean)
          });
          continue;
        }

        if (checks.length > 0) {
          result.push({
            label,
            field_type: 'checkbox_group',
            input_type: '',
            placeholder: '',
            options: checks
              .map(r => (r.innerText || r.textContent || '').trim().replace(/\\s+/g, ' '))
              .filter(Boolean)
          });
        }
      }

      return result;
    }
    """
    return page.evaluate(js)


def fill_input_by_label(page, label_text, value):
    js = """
    ({ labelText, value }) => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 &&
               s.display !== 'none' &&
               s.visibility !== 'hidden';
      }

      const items = Array.from(document.querySelectorAll('.el-form-item'));
      for (const item of items) {
        if (!visible(item)) continue;

        const labelEl = item.querySelector('.el-form-item__label');
        const label = labelEl ? (labelEl.innerText || labelEl.textContent || '').trim().replace(/\\s+/g, ' ') : '';
        if (!label || !label.includes(labelText)) continue;

        const input = item.querySelector('input, textarea');
        if (input && visible(input)) {
          input.scrollIntoView({ block: 'center' });
          input.focus();
          input.value = '';
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.value = String(value);
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.dispatchEvent(new Event('change', { bubbles: true }));
          return { ok: true, type: 'input' };
        }

        const select = item.querySelector('.el-select');
        if (select && visible(select)) {
          select.click();
          return { ok: true, type: 'select' };
        }

        const radios = Array.from(item.querySelectorAll('.el-radio'));
        if (radios.length > 0) {
          for (const r of radios) {
            const t = (r.innerText || r.textContent || '').trim().replace(/\\s+/g, ' ');
            if (t.includes(String(value))) {
              r.click();
              return { ok: true, type: 'radio' };
            }
          }
        }

        const checks = Array.from(item.querySelectorAll('.el-checkbox'));
        if (checks.length > 0 && Array.isArray(value)) {
          for (const v of value) {
            for (const c of checks) {
              const t = (c.innerText || c.textContent || '').trim().replace(/\\s+/g, ' ');
              if (t.includes(String(v))) {
                c.click();
              }
            }
          }
          return { ok: true, type: 'checkbox' };
        }
      }
      return { ok: false };
    }
    """
    result = page.evaluate(js, {"labelText": label_text, "value": value})
    if not result["ok"]:
        raise RuntimeError(f"未找到字段: {label_text}")

    if result["type"] == "select":
        wait(page, 800)
        pop = page.locator(".el-select-dropdown:visible, .el-popper:visible").last
        option = pop.locator(
            ".el-select-dropdown__item:visible, .el-tree-node__content:visible, .el-tree-node:visible, li:visible, div:visible"
        ).filter(has_text=str(value)).first
        option.wait_for(state="visible", timeout=10000)
        option.click()
        wait(page, 800)

    wait(page, 500)


def click_execute(page):
    btn = page.locator("button:visible").filter(has_text="执行").first
    btn.wait_for(state="visible", timeout=20000)
    btn.click()
    wait(page, 10000)


def extract_result(page):
    body = page.locator("body").inner_text(timeout=10000)
    return body[-8000:]

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


def open_command_processing(page):
    click_top_menu(page, "配置")
    click_popup_item(page, "命令处理")
    wait(page, 5000)

    choose_network_element(page, TASK["network_element"])
    wait(page, 4000)

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

        open_command_processing(page)
        click_tree_path(page, TASK["path_by_name"])
        wait(page, 2000)

        fields = extract_form_fields(page)
        print("识别到的表单字段：")
        print(json.dumps(fields, ensure_ascii=False, indent=2))

        for label, value in TASK["params"].items():
            fill_input_by_label(page, label, value)

        click_execute(page)

        result = extract_result(page)
        print("\n===== 执行结果 =====")
        print(result)

        browser.close()


if __name__ == "__main__":
    main()
