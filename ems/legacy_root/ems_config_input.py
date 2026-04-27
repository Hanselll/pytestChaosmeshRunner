# -*- coding: utf-8 -*-
from playwright.sync_api import sync_playwright

COOKIE_STRING = r"""grafana_session=375ebbf8a684e2edf91eb31fbcca6345; grafana_session_expiry=1772268912; rememberMe=true; username=SXlpr7Diy6dbcTLKq2oWWgEBX8VAJ5vKTRKFn8Emi+dU5CDVy67jNAOjuE+eykeUtlMWBAU1EEIf0IyXAIXdf6S/cWmQZX5adfOrNImsCHSzIOO8lgxYLLV8nWUDM5vNLx3g9HR9WiPSyR938o0XkfaD5/M8q7tJpRrIcamvBhIIxbOIHz/L/rXUdt12CLS+4RNZ+f17hFIoI2wV3LZJXQcBRoxTwwUnBAvn8CnGZpld3HidNucYijtKv/XmJvRn1aiE6FQE75CB2GevNYimqlFgrfvh7Bmuuh9Pii4CcG/z60r8JS3WQ+DrMIXSHMtVqHoc/cKErpXvXqqQizC6xA==; password=ZHuwup+Q6oOBz9PBqXZKXXD8qLa1ej4S67D3nPkqMzpfxsdOXyQ5NtTdBcZwQp/psa8E6ju81ICrtiNEezQkgWDyycNA23fbqHCgW350cEFUryWLAIxmE6M9EfTGe6Lgm4mStaIgBtV2wtEyL7m1otdTIRzukgfFQ7FlhzldA+e1m66MYMRaYq6T/Xkgepw5eijVYHpwJCnZwVc3ToLmJz0dQZfuqB/gUOTvwGt3U3vXgAAPo9rMQVxsfx9bmc2SsSnQvHm9URzCbd9OGcmH8XnLV4eMBh2AKbD/sXuAyR9NffRK0G0xRtu/DBIubZR/jcpPEN1eH89kN0iSldZ0WQ==; JSESSIONID=52ed6a64-1238-438b-896e-94a25829fcfc; HFLAG=true; oldPassword=OQo46BCqTdOfd8hn9yWlrR/djnKo4gJpWFu+6p5kCGXoNn1U2aG37eSb2qImNx+I7Kq3/+shdL92LgS5P5DxXqi3RkmDpJO7pqJ/cFCfcyRivAJjbo5gpEc72410ys0Ub9SLloJUO4DUIqdaQ1tU0W3rKoIay8sotsy6c0CFfJE3eUcKIYSVqj3iH4SHhDDoJP3Z1WIvxji/ms/p53PlubbbashZ1vH3VmAiex63QbhsBuMam2am9/fJgtUMvpE4bBHo+YYkfRRxu7PLfRB2dK0zoNfOoLrHa5ppUJr3DTtjiEb6fXFwQebvOTTBnuxCTWJB7qvtbjn1x6rMrXK64A=="""

BASE_URL = "https://127.0.0.1:50443"
TARGET_URL = BASE_URL + "/index#/topoOverview"
NE_IP = "10.230.4.248"

# 按实际页面修改这里
FORM_VALUES_BY_LABEL = {
    # 例子，字段名要改成页面真实显示的中文
    # "DNN名称": "internet",
    # "VRF ID": "50001",
}

# 如果字段名不好定位，可以按“第几个输入框”填
FORM_VALUES_BY_INDEX = {
    # 例子：第0个输入框填 internet，第1个输入框填 50001
    # 0: "internet",
    # 1: "50001",
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


def click_tree_text(page, text):
    candidates = [
        page.locator(".el-tree-node__content:visible").filter(has_text=text),
        page.locator(".custom-label:visible").filter(has_text=text),
        page.locator(".el-tooltip:visible").filter(has_text=text),
        page.locator(".el-tree-node:visible").filter(has_text=text),
        page.locator("div:visible").filter(has_text=text),
    ]

    for loc in candidates:
        count = loc.count()
        if count <= 0:
            continue
        for i in range(min(count, 5)):
            node = loc.nth(i)
            try:
                node.wait_for(state="visible", timeout=3000)
                node.scroll_into_view_if_needed()
                node.click()
                wait(page, 1500)
                return
            except Exception:
                continue

    raise RuntimeError(f"未找到树节点: {text}")


def open_target_command(page):
    click_top_menu(page, "配置")
    click_popup_item(page, "命令处理")
    wait(page, 5000)

    choose_network_element(page, NE_IP)
    wait(page, 4000)

    click_tree_text(page, "业务开通配置")
    click_tree_text(page, "DNN配置")
    click_tree_text(page, "增加DNN VRF映射配置")
    wait(page, 3000)


def dump_visible_inputs(page):
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
      const inputs = Array.from(document.querySelectorAll('input, textarea'));
      inputs.forEach((el, idx) => {
        if (!visible(el)) return;
        const formItem = el.closest('.el-form-item');
        let label = '';
        if (formItem) {
          const labelEl = formItem.querySelector('.el-form-item__label');
          if (labelEl) label = (labelEl.innerText || labelEl.textContent || '').trim().replace(/\\s+/g, ' ');
        }
        result.push({
          index: idx,
          tag: el.tagName,
          type: el.getAttribute('type') || '',
          placeholder: el.getAttribute('placeholder') || '',
          value: el.value || '',
          label: label
        });
      });
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

      const formItems = Array.from(document.querySelectorAll('.el-form-item'));
      for (const item of formItems) {
        if (!visible(item)) continue;

        const labelEl = item.querySelector('.el-form-item__label');
        const label = labelEl ? (labelEl.innerText || labelEl.textContent || '').trim().replace(/\\s+/g, ' ') : '';
        if (!label.includes(labelText)) continue;

        const input = item.querySelector('input, textarea');
        if (!input || !visible(input)) continue;

        input.scrollIntoView({ block: 'center' });
        input.focus();
        input.value = '';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.value = value;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
      }
      return false;
    }
    """
    ok = page.evaluate(js, {"labelText": label_text, "value": value})
    if not ok:
        raise RuntimeError(f"未找到标签对应输入框: {label_text}")
    wait(page, 800)


def fill_input_by_index(page, input_index, value):
    locator = page.locator("input:visible, textarea:visible").nth(input_index)
    locator.wait_for(state="visible", timeout=10000)
    locator.click()
    locator.fill(value)
    wait(page, 800)


def click_execute(page):
    btn = page.locator("button:visible").filter(has_text="执行").first
    btn.wait_for(state="visible", timeout=20000)
    btn.click()
    wait(page, 8000)


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

        open_target_command(page)

        # 调试：打印当前页面可见输入框
        inputs = dump_visible_inputs(page)
        print("当前页面可见输入框：")
        for item in inputs:
            print(item)

        # 方式1：按字段名填写
        for label_text, value in FORM_VALUES_BY_LABEL.items():
            fill_input_by_label(page, label_text, value)

        # 方式2：按输入框顺序填写
        for idx, value in FORM_VALUES_BY_INDEX.items():
            fill_input_by_index(page, idx, value)

        # 执行
        click_execute(page)

        body = page.locator("body").inner_text(timeout=10000)
        print("\n===== 执行后页面文本尾部 =====")
        print(body[-5000:])

        browser.close()


if __name__ == "__main__":
    main()
