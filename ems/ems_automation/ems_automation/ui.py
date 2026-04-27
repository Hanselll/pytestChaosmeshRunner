from __future__ import annotations

from pathlib import Path

from .settings import HOME_URL


def wait(page, ms: int = 1200) -> None:
    page.wait_for_timeout(ms)


def wait_for_condition(page, script: str, timeout: int = 10000, polling: int = 200) -> None:
    page.wait_for_function(script, timeout=timeout, polling=polling)


def _detect_page_state(page) -> dict:
    body = ""
    try:
        body = page.locator("body").inner_text(timeout=5000)
    except Exception:
        pass
    normalized = " ".join(body.split())
    url = page.url
    lower_url = url.lower()
    lower_body = normalized.lower()

    if "#/login" in lower_url or "账号登录" in normalized or "登录" in normalized:
        return {"state": "login", "url": url, "body": normalized}

    if (
        "oops!" in lower_body
        or "you can not enter this page" in lower_body
        or "登录状态已过期" in normalized
    ):
        return {"state": "expired", "url": url, "body": normalized}

    return {"state": "ok", "url": url, "body": normalized}


def open_home(page) -> None:
    last_error = None
    for attempt in range(3):
        try:
            page.goto(HOME_URL, wait_until="load", timeout=120000)
            if attempt > 0:
                wait(page, 500)
                page.reload(wait_until="load", timeout=120000)

            page.locator("body").wait_for(state="visible", timeout=30000)
            page_state = _detect_page_state(page)
            if page_state["state"] == "login":
                raise RuntimeError(
                    "EMS session is not logged in. Run `ems-cli auth-login` or update EMS cookie."
                    f"\nURL: {page_state['url']}\nBody: {page_state['body'][:300]}"
                )
            if page_state["state"] == "expired":
                raise RuntimeError(
                    "EMS session appears expired or unauthorized. Run `ems-cli auth-login` or update EMS cookie."
                    f"\nURL: {page_state['url']}\nBody: {page_state['body'][:300]}"
                )
            page.wait_for_function(
                """
                () => {
                  const body = document.body;
                  const app = document.querySelector('#app');
                  const progress = document.querySelector('#nprogress .bar');
                  const bodyText = (body?.innerText || '').trim();
                  const appText = (app?.innerText || '').trim();
                  const appChildren = app ? app.children.length : 0;
                  const readyTokens = ['总览', '配置', '告警', '门户', 'XSpace'];
                  const hasToken = readyTokens.some(token => bodyText.includes(token) || appText.includes(token));
                  const progressHidden = !progress || getComputedStyle(progress).display === 'none';
                  return (hasToken || appChildren > 0) && progressHidden;
                }
                """,
                timeout=45000,
            )
            return
        except Exception as exc:
            last_error = exc
    raise last_error


def dump_debug(page, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        body_text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        body_text = ""
    path.with_suffix(".txt").write_text(body_text, encoding="utf-8")
    try:
        path.with_suffix(".html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    try:
        page.screenshot(path=str(path.with_suffix(".png")), full_page=True)
    except Exception:
        pass


def click_top_menu(page, name: str) -> None:
    selectors = [
        ".el-submenu__title:visible",
        ".el-menu-item:visible",
        "span:visible",
        "div:visible",
    ]
    for delay in (0, 300, 800, 1500):
        if delay:
            wait(page, delay)
        for selector in selectors:
            loc = page.locator(selector).filter(has_text=name)
            try:
                count = min(loc.count(), 20)
            except Exception:
                continue
            if count <= 0:
                continue
            for index in range(count):
                node = loc.nth(index)
                try:
                    node.wait_for(state="visible", timeout=3000)
                    try:
                        node.hover()
                    except Exception:
                        node.click()
                    wait(page, 200)
                    return
                except Exception:
                    continue

    js = """
    ({ name }) => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }
      const nodes = Array.from(document.querySelectorAll('.el-submenu__title, .el-menu-item, span, div'));
      for (const node of nodes) {
        if (!visible(node)) continue;
        const text = (node.innerText || node.textContent || '').trim().replace(/\\s+/g, ' ');
        if (!text || !text.includes(name)) continue;
        node.scrollIntoView({ block: 'center' });
        node.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
        node.click();
        return { ok: true };
      }
      return { ok: false };
    }
    """
    result = page.evaluate(js, {"name": name})
    if result.get("ok"):
        wait(page, 200)
        return

    body = page.locator("body").inner_text(timeout=5000)
    raise RuntimeError(f"Top menu not found: {name}\n{body[:1000]}")


def click_popup_item(page, name: str) -> None:
    item = page.locator(".el-menu--popup .el-menu-item").filter(has_text=name).first
    item.wait_for(state="visible", timeout=10000)
    item.click()
    page.locator(".el-menu--popup").first.wait_for(state="hidden", timeout=5000)


def click_text(page, text: str, exact: bool = False) -> bool:
    selectors = [
        "button:visible",
        ".el-button:visible",
        ".el-link:visible",
        "a:visible",
        "span:visible",
        "div:visible",
    ]
    matched = []
    for selector in selectors:
        loc = page.locator(selector)
        count = min(loc.count(), 400)
        for i in range(count):
            node = loc.nth(i)
            try:
                raw = node.inner_text(timeout=300)
            except Exception:
                continue
            normalized = " ".join(raw.split())
            if not normalized:
                continue
            ok = normalized == text if exact else text in normalized
            if ok:
                matched.append((len(normalized), node))

    matched.sort(key=lambda x: x[0])
    for _, node in matched[:8]:
        try:
            node.scroll_into_view_if_needed()
            node.click()
            wait(page, 150)
            return True
        except Exception:
            continue
    return False


def _network_element_match_texts(spec) -> list[str]:
    if isinstance(spec, str) and spec.strip():
        return [spec.strip()]
    if isinstance(spec, dict):
        values = spec.get("match_texts", [])
        if isinstance(values, list):
            texts = [str(item).strip() for item in values if str(item).strip()]
            if texts:
                return texts
        for key in ("display_name", "ip", "alias", "match_text", "ref"):
            value = spec.get(key)
            if isinstance(value, str) and value.strip():
                return [value.strip()]
    raise RuntimeError(f"Invalid network element spec: {spec!r}")


def list_network_elements(page) -> list[dict]:
    js = """
    () => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }
      const seen = new Set();
      const selectors = [
        '.el-select-dropdown .el-tree-node__content',
        '.el-select-dropdown .custom-tree-icon',
        '.el-select-dropdown .el-tree-node',
        '.el-select-dropdown__item',
        '.el-popper .el-tree-node__content',
        '.el-popper .custom-tree-icon',
        '.el-popper .el-tree-node',
        '.el-popper .el-select-dropdown__item'
      ];
      const items = [];
      for (const selector of selectors) {
        for (const node of Array.from(document.querySelectorAll(selector))) {
          if (!visible(node)) continue;
          const text = (node.innerText || node.textContent || '').trim().replace(/\\s+/g, ' ');
          if (!text || seen.has(text)) continue;
          seen.add(text);
          items.push({ text });
        }
      }
      return items;
    }
    """
    return page.evaluate(js)


def choose_network_element(page, ne_spec) -> None:
    match_texts = _network_element_match_texts(ne_spec)
    js_click_ne = """
    ({ matchTexts }) => {
      function visible(el) {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }
      const selectors = [
        '.el-select-dropdown .el-tree-node__content',
        '.el-select-dropdown .custom-tree-icon',
        '.el-select-dropdown .el-tree-node',
        '.el-select-dropdown__item',
        '.el-popper .el-tree-node__content',
        '.el-popper .custom-tree-icon',
        '.el-popper .el-tree-node',
        '.el-popper .el-select-dropdown__item'
      ];
      for (const selector of selectors) {
        for (const node of Array.from(document.querySelectorAll(selector))) {
          if (!visible(node)) continue;
          const text = (node.innerText || node.textContent || '').trim().replace(/\\s+/g, ' ');
          if (!text || !matchTexts.some(item => text.includes(item) || item.includes(text))) continue;
          node.scrollIntoView({ block: 'center' });
          node.click();
          return { ok: true, matched: text };
        }
      }
      return { ok: false, matched: '' };
    }
    """

    for _ in range(3):
        try:
            page.locator(".el-select:visible").first.click()
        except Exception:
            page.evaluate(
                """
                () => {
                  const select = document.querySelector('.el-select');
                  if (select) select.click();
                }
                """
            )
        wait_for_condition(
            page,
            """
            () => {
              const nodes = document.querySelectorAll('.el-select-dropdown, .el-popper');
              return Array.from(nodes).some(node => {
                const style = getComputedStyle(node);
                return style.display !== 'none' && style.visibility !== 'hidden';
              });
            }
            """,
            timeout=3000,
        )

        result = page.evaluate(js_click_ne, {"matchTexts": match_texts})
        if result.get("ok"):
            wait(page, 150)
            return

    available = list_network_elements(page)
    preview = ", ".join(item["text"] for item in available[:10])
    raise RuntimeError(f"未找到网元选项: {match_texts}. available={preview}")


def fill_form_item(page, label: str, value) -> None:
    normalized_label = " ".join(str(label).split())
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
      const items = Array.from(document.querySelectorAll('.el-form-item'));
      for (const item of items) {
        if (!visible(item)) continue;
        const labelEl = item.querySelector('.el-form-item__label');
        const label = labelEl ? normalize(labelEl.innerText || labelEl.textContent || '') : '';
        if (!label.includes(labelText)) continue;

        const input = item.querySelector('input, textarea');
        if (input && visible(input)) {
          input.scrollIntoView({ block: 'center' });
          input.focus();
          const prototype = input.tagName.toLowerCase() === 'textarea'
            ? window.HTMLTextAreaElement.prototype
            : window.HTMLInputElement.prototype;
          const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
          const setter = descriptor && descriptor.set;
          if (setter) {
            setter.call(input, '');
            input.dispatchEvent(new Event('input', { bubbles: true }));
            setter.call(input, String(value));
          } else {
            input.value = String(value);
          }
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.dispatchEvent(new Event('change', { bubbles: true }));
          input.blur();
          return { ok: true, type: input.tagName.toLowerCase() };
        }

        const select = item.querySelector('.el-select');
        if (select && visible(select)) {
          select.click();
          return { ok: true, type: 'select' };
        }
      }
      return { ok: false, type: '' };
    }
    """
    result = {"ok": False, "type": ""}
    for _ in range(5):
        try:
            result = page.evaluate(js, {"labelText": normalized_label, "value": value})
        except Exception:
            result = {"ok": False, "type": ""}
        if result["ok"]:
            break
        wait(page, 250)
    if not result["ok"]:
        try:
            available = page.evaluate(
                """
                () => Array.from(document.querySelectorAll('.el-form-item__label'))
                  .map(node => (node.innerText || node.textContent || '').trim().replace(/\\s+/g, ' '))
                  .filter(Boolean)
                """
            )
        except Exception:
            available = []
        preview = ", ".join(available[:12])
        raise RuntimeError(f"Field not found: {label}. available={preview}")
    if result["type"] == "select":
        wait_for_condition(
            page,
            """
            () => {
              const nodes = document.querySelectorAll('.el-select-dropdown, .el-popper');
              return Array.from(nodes).some(node => {
                const style = getComputedStyle(node);
                return style.display !== 'none' && style.visibility !== 'hidden';
              });
            }
            """,
            timeout=3000,
        )
        option = page.locator(
            ".el-select-dropdown:visible .el-select-dropdown__item:visible, "
            ".el-popper:visible .el-select-dropdown__item:visible, "
            ".el-popper:visible li:visible, "
            ".el-popper:visible div:visible"
        ).filter(has_text=str(value)).first
        option.wait_for(state="visible", timeout=10000)
        option.click()
        wait(page, 150)
