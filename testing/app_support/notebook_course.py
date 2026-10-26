"""Real Help course-control contract, shared by short and installed checks.

Run against a current GUI server (no payload, kernel or native bridge needed):
    uv run --locked python -m testing.app_support.notebook_course http://127.0.0.1:5173
"""

from __future__ import annotations

import os
import re
import sys

from playwright.sync_api import Locator, Page, expect, sync_playwright


def course_control(page: Page) -> Locator:
    return page.get_by_role("combobox", name="课程", exact=True)


def verify_course_control(page: Page) -> None:
    """Check the rendered product, before any expensive course preparation."""
    expect(course_control(page)).to_have_value("parameters")
    course_control(page).select_option("groups")
    expect(course_control(page)).to_have_value("groups")
    expect(page).to_have_url(re.compile(r"[?&]lesson=groups(?:&[^#]*)?#help$"))
    expect(
        page.get_by_role("heading", name="分组分析与历史", exact=True)
    ).to_be_visible()
    page.reload()
    expect(course_control(page)).to_have_value("groups")
    course_control(page).select_option("parameters")
    expect(course_control(page)).to_have_value("parameters")
    expect(page.get_by_role("heading", name="参数与扫描", exact=True)).to_be_visible()


if __name__ == "__main__":
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            headless=True,
            args=["--no-sandbox"],
            executable_path=os.environ.get("SCOPECAT_TEST_CHROMIUM"),
        )
        page = browser.new_page()
        page.set_default_timeout(10_000)
        page.goto(sys.argv[1].rstrip("/") + "/#help")
        verify_course_control(page)
        browser.close()
        print("Help course control passed (browser only; no kernel/native execution).")
