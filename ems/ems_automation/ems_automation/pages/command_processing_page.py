from __future__ import annotations

from pathlib import Path
from typing import Any

from ..inventory import resolve_network_element_spec
from ..ui import (
    choose_network_element,
    click_popup_item,
    click_text,
    click_top_menu,
    dump_debug,
    fill_form_item,
    list_network_elements,
    open_home,
    wait,
    wait_for_condition,
)


class CommandProcessingPage:
    MENU_CONFIG = "配置"
    MENU_COMMAND_PROCESSING = "命令处理"
    BUTTON_EXECUTE = "执行"

    def __init__(self, page: Any) -> None:
        self.page = page

    def open_menu(self) -> None:
        open_home(self.page)
        click_top_menu(self.page, self.MENU_CONFIG)
        click_popup_item(self.page, self.MENU_COMMAND_PROCESSING)
        wait_for_condition(
            self.page,
            """
            () => {
              const tree = document.querySelector('.el-tree');
              const select = document.querySelector('.el-select');
              return !!tree && !!select;
            }
            """,
            timeout=8000,
        )

    def open(self, network_element) -> None:
        self.open_menu()
        if isinstance(network_element, dict) and any(key in network_element for key in ("match_texts", "display_name", "ip", "alias", "ref")):
            spec = network_element
        elif isinstance(network_element, str):
            spec = {"network_element": network_element}
        else:
            spec = network_element
        choose_network_element(self.page, resolve_network_element_spec(spec) if isinstance(spec, dict) and any(key in spec for key in ("network_element", "network_element_ref")) else spec)
        wait(self.page, 200)

    def list_network_elements(self) -> list[dict]:
        self.open_menu()
        try:
            self.page.locator(".el-select:visible").first.click()
        except Exception:
            self.page.evaluate(
                """
                () => {
                  const select = document.querySelector('.el-select');
                  if (select) select.click();
                }
                """
            )
        wait(self.page, 150)
        return list_network_elements(self.page)

    def click_tree_path(self, path_by_name: list[str]) -> None:
        js = """
        ({ path }) => {
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
            result = self.page.evaluate(js, {"path": prefix})
            if not result["ok"]:
                raise RuntimeError(f"Unable to click tree node: {result['missing']}")
            if idx == len(path_by_name) - 1:
                wait_for_condition(
                    self.page,
                    """
                    () => {
                      const form = document.querySelector('.el-form');
                      const labels = document.querySelectorAll('.el-form-item__label');
                      const result = document.querySelector('.el-table, pre, textarea, .result, .mml-result');
                      return (!!form && labels.length > 0) || !!result;
                    }
                    """,
                    timeout=5000,
                )
            else:
                wait(self.page, 150)

    def expand_all_tree_nodes(self, rounds: int = 6) -> None:
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
            self.page.evaluate(js_scroll_top)
            wait(self.page, 100)
            while True:
                result = self.page.evaluate(js_expand)
                if result["expanded"] > 0:
                    wait(self.page, 120)
                info = self.page.evaluate(js_scroll_down)
                wait(self.page, 80)
                if not info or info["after"] == info["before"]:
                    break
            self.page.evaluate(js_scroll_top)
            wait(self.page, 100)

    def extract_tree_dom(self) -> list[dict]:
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
        return self.page.evaluate(js)

    def extract_form_fields(self) -> list[dict]:
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
                field_name: labelEl ? (labelEl.getAttribute('for') || '') : '',
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
                field_name: labelEl ? (labelEl.getAttribute('for') || '') : '',
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
        return self.page.evaluate(js)

    def open_form(self, network_element: dict, path_by_name: list[str]) -> list[dict]:
        self.open(network_element)
        self.click_tree_path(path_by_name)
        return self.extract_form_fields()

    def fill_form(self, params: dict[str, Any]) -> None:
        for label, value in params.items():
            fill_form_item(self.page, label, value)

    def set_command_text(self, command_text: str) -> None:
        js = """
        ({ value }) => {
          const textarea = document.querySelector('.cmd-textarea textarea, .cmd-textarea .el-textarea__inner, textarea.el-textarea__inner');
          if (!textarea) return { ok: false };
          const descriptor = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
          const setter = descriptor && descriptor.set;
          if (setter) {
            setter.call(textarea, value);
          } else {
            textarea.value = value;
          }
          textarea.dispatchEvent(new Event('input', { bubbles: true }));
          textarea.dispatchEvent(new Event('change', { bubbles: true }));
          textarea.blur();
          return { ok: true };
        }
        """
        result = self.page.evaluate(js, {"value": command_text})
        if not result.get("ok"):
            raise RuntimeError("Command textarea not found.")
        wait(self.page, 150)

    def execute(self) -> None:
        js = """
        ({ text }) => {
          function visible(el) {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
          }
          function norm(value) {
            return String(value || '').trim().replace(/\\s+/g, ' ');
          }
          const form = document.querySelector('.el-form');
          const formRect = form ? form.getBoundingClientRect() : null;
          const candidates = Array.from(document.querySelectorAll('button, .el-button')).filter(node => {
            if (!visible(node)) return false;
            const label = norm(node.innerText || node.textContent || '');
            return label === text || label.includes(text);
          });
          if (!candidates.length) return { ok: false, reason: 'no_button' };

          let chosen = null;
          let bestScore = Number.POSITIVE_INFINITY;
          for (const node of candidates) {
            const rect = node.getBoundingClientRect();
            let score = rect.top;
            if (formRect) {
              const verticalDistance = Math.abs(rect.top - formRect.bottom);
              const samePanelBoost = rect.top >= formRect.top ? 0 : 100000;
              score = samePanelBoost + verticalDistance;
            }
            if (score < bestScore) {
              bestScore = score;
              chosen = node;
            }
          }
          if (!chosen) return { ok: false, reason: 'no_candidate' };
          chosen.scrollIntoView({ block: 'center' });
          chosen.click();
          return { ok: true, label: norm(chosen.innerText || chosen.textContent || '') };
        }
        """
        result = self.page.evaluate(js, {"text": self.BUTTON_EXECUTE})
        if result.get("ok"):
            wait(self.page, 150)
            return
        if click_text(self.page, self.BUTTON_EXECUTE):
            return
        raise RuntimeError("Execute button not found.")

    def dump_debug(self, path: Path) -> None:
        dump_debug(self.page, path)
