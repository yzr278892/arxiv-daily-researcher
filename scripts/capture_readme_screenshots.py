#!/usr/bin/env python3
"""Capture privacy-safe screenshots from the modern WebUI for the README.

The script avoids API and notification pages unless explicitly run against an
isolated fixture with ADR_SCREENSHOT_ISOLATED=1. The backup image hides the
WebDAV card because a local installation can contain an endpoint or account
name there. Credentials are accepted only through environment variables and
are never written to an image or to stdout.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


BASE_URL = os.environ.get("ADR_SCREENSHOT_BASE_URL", "http://127.0.0.1:8501").rstrip("/")
OUTPUT_DIR = Path(
    os.environ.get("ADR_SCREENSHOT_OUTPUT_DIR", str(Path(__file__).resolve().parents[1] / "assets"))
)
VIEWPORT = {"width": 1440, "height": 980}


def wait_for_modern_ui(page: Page, timeout_ms: int = 30_000) -> None:
    """Wait for the page renderer rather than relying on network-idle."""

    page.wait_for_selector("#page-root > *", timeout=timeout_ms)
    page.wait_for_timeout(700)


def authenticate(page: Page) -> None:
    """Sign in when the capture target has panel authentication enabled."""

    if not page.locator("#auth:not([hidden])").count():
        return
    if page.locator("#setup-form:not([hidden])").count():
        raise RuntimeError("WebUI administrator account is not initialized. Complete local setup first.")
    if not page.locator("#login-form:not([hidden])").count():
        return
    username = os.environ.get("ADR_SCREENSHOT_USERNAME", "")
    password = os.environ.get("ADR_SCREENSHOT_PASSWORD", "")
    if not username or not password:
        raise RuntimeError(
            "WebUI authentication is enabled; set ADR_SCREENSHOT_USERNAME and ADR_SCREENSHOT_PASSWORD."
        )
    page.locator("#login-username").fill(username)
    page.locator("#login-password").fill(password)
    page.locator("#login-form button[type='submit']").click()
    page.wait_for_selector("#app:not([hidden])", timeout=30_000)
    wait_for_modern_ui(page)


def show_page(page: Page, group: str, page_name: str) -> None:
    page.goto(f"{BASE_URL}/#{group}/{page_name}", wait_until="domcontentloaded", timeout=30_000)
    wait_for_modern_ui(page)
    page.evaluate("window.scrollTo(0, 0)")


def capture(page: Page, filename: str) -> None:
    page.screenshot(path=str(OUTPUT_DIR / filename), full_page=False)
    print(f"captured {filename}")


def hide_card(page: Page, title: str) -> None:
    """Hide a card whose content could contain local connection metadata."""

    card = page.locator(".section-card", has=page.get_by_role("heading", name=title)).first
    if card.count():
        card.evaluate("node => { node.style.display = 'none'; }")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(400)
        authenticate(page)

        show_page(page, "run", "daily_research")
        capture(page, "webui_daily_push_v4.png")

        show_page(page, "system", "analytics")
        capture(page, "webui_analytics_v4.png")

        show_page(page, "configuration", "scoring")
        capture(page, "webui_scoring_v4.png")

        if os.environ.get("ADR_SCREENSHOT_ISOLATED") == "1":
            if not os.environ.get("ADR_SCREENSHOT_BASE_URL"):
                raise RuntimeError("Set ADR_SCREENSHOT_BASE_URL to an isolated demo WebUI.")
            show_page(page, "configuration", "api")
            page.locator("#semantic-dependent").scroll_into_view_if_needed()
            capture(page, "webui_api_semantic_v4.png")

        show_page(page, "system", "backup_sync")
        hide_card(page, "WebDAV")
        page.locator(".section-card", has=page.get_by_role("heading", name="本地备份")).scroll_into_view_if_needed()
        page.wait_for_timeout(250)
        capture(page, "webui_data_management_v4.png")

        show_page(page, "system", "history_tasks")
        capture(page, "webui_history_import_v4.png")

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
