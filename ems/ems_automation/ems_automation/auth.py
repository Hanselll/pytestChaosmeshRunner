from __future__ import annotations

import json
from pathlib import Path
import os

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from .settings import BASE_URL, DEFAULT_VIEWPORT, HOME_URL, get_auth_state_path, load_cookie_string
from .ui import open_home
from urllib.parse import urlparse


def _has_storage_state(path: Path | None = None) -> bool:
    target = path or get_auth_state_path()
    return target.exists() and target.is_file() and target.stat().st_size > 0


def _storage_state_matches_base_url(path: Path) -> bool:
    host = urlparse(BASE_URL).hostname or ""
    if not host:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    for cookie in data.get("cookies", []) or []:
        domain = str((cookie or {}).get("domain") or "").lstrip(".")
        if domain == host:
            return True
    for origin in data.get("origins", []) or []:
        origin_host = urlparse(str((origin or {}).get("origin") or "")).hostname or ""
        if origin_host == host:
            return True
    return False


def _build_context(browser: Browser, storage_state_path: Path | None = None) -> BrowserContext:
    target = storage_state_path or get_auth_state_path()
    context_options = {
        "ignore_https_errors": True,
        "viewport": DEFAULT_VIEWPORT,
        "accept_downloads": True,
    }
    env_cookie = os.getenv("EMS_COOKIE", "").strip()
    if _has_storage_state(target) and not env_cookie and _storage_state_matches_base_url(target):
        context_options["storage_state"] = str(target)
        return browser.new_context(**context_options)

    context = browser.new_context(**context_options)
    try:
        cookie_string = load_cookie_string()
    except Exception:
        return context

    from .settings import build_cookies

    context.add_cookies(build_cookies(cookie_string))
    return context


def create_browser_context(headless: bool = True, storage_state_path: Path | None = None) -> tuple[object, Browser, BrowserContext]:
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(channel="msedge", headless=headless)
    context = _build_context(browser, storage_state_path)
    return playwright, browser, context


def open_page_from_context(context: BrowserContext) -> Page:
    return context.new_page()


def save_storage_state(context: BrowserContext, output_path: Path | None = None) -> Path:
    target = output_path or get_auth_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(target))
    return target


def run_manual_login(output_path: Path | None = None, start_url: str = HOME_URL) -> dict:
    target = output_path or get_auth_state_path()
    playwright, browser, context = create_browser_context(headless=False, storage_state_path=target)
    page = context.new_page()
    try:
        page.goto(start_url, wait_until="load", timeout=120000)
        print("Browser opened for EMS login.")
        print("Complete username/password and puzzle verification in the browser window.")
        try:
            input("After the EMS home page is fully loaded, press Enter here to save the login state...")
        except EOFError as exc:
            raise RuntimeError(
                "EMS manual login requires an interactive terminal. "
                "Run auth-login from a real console before starting the case."
            ) from exc
        open_home(page)
        current_url = page.url
        body_text = page.locator("body").inner_text(timeout=5000)

        saved_path = save_storage_state(context, target)
        return {
            "mode": "manual_login",
            "status": "saved",
            "storage_state": str(saved_path),
            "url": current_url,
            "body_tail": body_text[-500:],
        }
    finally:
        browser.close()
        playwright.stop()


def run_auth_check(output_path: Path | None = None, start_url: str = HOME_URL) -> dict:
    target = output_path or get_auth_state_path()
    playwright, browser, context = create_browser_context(headless=True, storage_state_path=target)
    page = context.new_page()
    try:
        page.goto(start_url, wait_until="load", timeout=120000)
        open_home(page)
        current_url = page.url
        body_text = page.locator("body").inner_text(timeout=5000)
        saved_path = save_storage_state(context, target)
        return {
            "mode": "auth_check",
            "status": "ok",
            "storage_state": str(saved_path),
            "url": current_url,
            "body_tail": body_text[-500:],
        }
    finally:
        browser.close()
        playwright.stop()
