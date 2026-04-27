from __future__ import annotations

from contextlib import contextmanager

from .auth import create_browser_context, open_page_from_context


@contextmanager
def open_page():
    playwright, browser, context = create_browser_context(headless=True)
    page = open_page_from_context(context)
    try:
        yield page
    finally:
        browser.close()
        playwright.stop()
