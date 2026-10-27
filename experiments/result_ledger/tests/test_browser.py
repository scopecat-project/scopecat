"""Local Chromium checks for retained reading during sparse publication/recovery."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Fixed local module and pytest-owned paths; no shell or external commands.
# ruff: noqa: S603


def test_reader_retains_selection_range_and_last_readable_data(tmp_path: Path) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    chromium = shutil.which("chromium")
    if chromium is None:
        pytest.skip("local Chromium is required; this test never downloads a browser")
    directory = tmp_path / "synthetic"
    command = [sys.executable, "-m", "experiments.result_ledger"]
    crashed = subprocess.run(
        [
            *command,
            "demo",
            "--directory",
            str(directory),
            "--crash-at",
            "group-publish",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert crashed.returncode == 75, crashed.stderr
    server = subprocess.Popen(
        [*command, "serve", "--directory", str(directory), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert server.stdout is not None
        url = server.stdout.readline().strip().split()[-1]
        assert url.startswith("http://127.0.0.1:")
        with playwright.sync_playwright() as engine:
            browser = engine.chromium.launch(executable_path=chromium, headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            page.wait_for_function(
                "document.querySelector('#group').options.length === 3"
            )
            page.select_option("#group", "high")
            page.select_option("#output", "waveform")
            page.select_option("#channel", "Q")
            page.fill("#xmin", "2")
            page.locator("#xmin").dispatch_event("change")
            page.fill("#xmax", "12")
            page.locator("#xmax").dispatch_event("change")
            assert "Published and readable" in page.locator("#readiness").inner_text()
            assert (
                "4 explicitly unavailable" in page.locator("#availability").inner_text()
            )
            assert page.locator("#chart polyline").count() == 2
            recovered = subprocess.run(
                [*command, "recover", "--directory", str(directory)],
                capture_output=True,
                text=True,
                check=True,
            )
            assert json.loads(recovered.stdout)["counts"]["points"] == 6
            page.wait_for_function(
                "document.querySelector('#analysis').textContent"
                ".includes('Exact input hash')"
            )
            page.reload()
            page.wait_for_function("document.querySelector('#chart polyline') !== null")
            assert page.locator("#group").input_value() == "high"
            assert page.locator("#output").input_value() == "waveform"
            assert page.locator("#channel").input_value() == "Q"
            assert page.locator("#xmin").input_value() == "2"
            assert page.locator("#xmax").input_value() == "12"
            evidence = page.locator("#evidence").text_content()
            assert '"notification_cursor": 3' in evidence
            server.terminate()
            server.wait(timeout=5)
            page.wait_for_function(
                "document.querySelector('#connection').textContent.includes('Disconnected')"
            )
            assert page.locator("#chart polyline").count() == 2
            assert page.locator("#group").input_value() == "high"
            assert not errors
            browser.close()
    finally:
        if server.poll() is None:
            server.terminate()
            server.wait(timeout=5)
        if server.stdout is not None:
            server.stdout.close()
