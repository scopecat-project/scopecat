"""Verify added Help courses against real Chromium and one application.

Usage: python scripts/verify_teaching_help_topics.py <fresh-work-dir> <gui-dist>
Requires development dependencies and Chromium (SCOPECAT_TEST_CHROMIUM supported).
This focused integration check substitutes author environment provisioning with
this development environment, and records external editor opens. It does not
claim installation, native window/editor, or Notebook kernel acceptance.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import httpx2
from playwright.sync_api import expect, sync_playwright

from lab_teaching.lessons import LessonTopic
from lab_tools.application_runtime import ApplicationRuntime
from lab_tools.desktop import DesktopAPI
from lab_tools.desktop_session import DesktopSession
from lab_tools.notebook_journey import NotebookJourney, current

if TYPE_CHECKING:
    import webview

TOPICS: tuple[LessonTopic, ...] = (
    "refresh",
    "compute",
    "calibration",
    "joint-calibration",
    "task-calibration",
)


def verify(work: Path, static: Path) -> None:
    if sys.platform == "linux":
        if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER")
    work.mkdir(parents=True, exist_ok=False)
    runtime = ApplicationRuntime(work / "application")
    runtime.configure(static_dir=static)
    session = DesktopSession(runtime, threading.Event())
    api = DesktopAPI(session, lambda: cast("webview.Window", cast("object", None)))
    opened: list[str] = []
    provisioned: list[Path] = []
    retained: dict[LessonTopic, tuple[NotebookJourney, bytes, bytes, bytes]] = {}

    def provision(_runtime: ApplicationRuntime, source: Path) -> Path:
        provisioned.append(source)
        # Deliberately substitute packaging only; registration and bridge stay real.
        (source / ".venv").symlink_to(Path(sys.prefix), target_is_directory=True)
        return Path(sys.executable)

    def record(journey: NotebookJourney) -> None:
        assert journey.notebook.is_file()
        opened.append(str(journey.notebook))

    def runs(endpoint: str) -> object:
        response = httpx2.get(endpoint + "/api/v1/runs", trust_env=False)
        response.raise_for_status()
        return cast("object", response.json())

    try:
        endpoint = runtime.start().base_url
        session.connected(endpoint)
        before = runs(endpoint)
        with (
            patch("lab_tools.notebook_journey.create_client_environment", provision),
            patch(
                "lab_tools.notebook_journey.prepare_execution_environment",
                return_value=Path(sys.executable),
            ) as execution,
            patch("lab_tools.notebook_journey.open_editor", record),
            sync_playwright() as driver,
        ):
            browser = driver.chromium.launch(
                headless=True,
                args=["--no-sandbox"],
                executable_path=os.environ.get("SCOPECAT_TEST_CHROMIUM"),
            )
            page = browser.new_page()
            page.set_default_timeout(60_000)
            expose = cast(
                "Callable[[str, Callable[..., object]], object]", page.expose_function
            )

            def status(topic: LessonTopic) -> dict[str, object] | None:
                return api.notebook_journey(topic)

            def prepare(
                parent: str | None, topic: LessonTopic, repair: bool = False
            ) -> dict[str, object]:
                return api.prepare_notebook_journey(parent, topic, repair)

            def editor(topic: LessonTopic) -> None:
                api.open_lesson_notebook(topic)

            expose("journeyStatus", status)
            expose("journeyPrepare", prepare)
            expose("journeyOpen", editor)
            page.add_init_script("""window.pywebview = {api: {
                notebook_journey: (topic) => window.journeyStatus(topic),
                prepare_notebook_journey: (parent, topic, repair = false) =>
                    window.journeyPrepare(parent ?? null, topic, repair),
                open_lesson_notebook: (topic) => window.journeyOpen(topic),
                set_window_title: async () => {},
            }};""")
            page.goto(endpoint + "/#help")
            page.set_viewport_size({"width": 1440, "height": 1100})
            courses = page.get_by_role("group", name="Choose a course")
            expect(courses.get_by_role("radio")).to_have_count(7)
            first = courses.get_by_role("radio", name="Parameters and scans")
            first.focus()
            first.press("ArrowRight")
            selected = courses.get_by_role("radio", name="Edit and refresh experiments")
            expect(selected).to_be_checked()
            expect(selected).to_be_focused()
            selected.press("ArrowRight")
            expect(
                courses.get_by_role("radio", name="Mean IQ and typed results")
            ).to_be_focused()
            assert not opened and not provisioned
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(work / "course-browser-start.png"), full_page=True)
            source_ids: set[str] = set()
            for topic in TOPICS:
                page.locator(f'input[type="radio"][value="{topic}"]').press("Space")
                page.get_by_role(
                    "button", name=f"Start {topic} Notebook", exact=True
                ).click()
                expect(
                    page.get_by_role(
                        "button", name=f"Continue {topic} Notebook", exact=True
                    )
                ).to_be_enabled()
                journey = current(runtime, topic)
                assert journey is not None and journey.ready
                assert opened[-1] == str(journey.notebook)
                expect(
                    page.get_by_text(str(journey.notebook), exact=True)
                ).to_be_visible()
                source_ids.add(runtime.source(journey.directory))
                document = cast(
                    "dict[str, object]", json.loads(journey.notebook.read_text())
                )
                cast("list[object]", document["cells"]).append(
                    {
                        "cell_type": "markdown",
                        "id": "retained-user-note",
                        "metadata": {},
                        "source": [f"My retained {topic} notes"],
                    }
                )
                journey.notebook.write_text(json.dumps(document, indent=1) + "\n")
                code = journey.directory / "src/my_experiment/setup.py"
                code.write_text(
                    code.read_text() + f"\n# Retained {topic} source edit\n"
                )
                receipt = runtime.home / f"learning/{topic}.json"
                retained[topic] = (
                    journey,
                    journey.notebook.read_bytes(),
                    code.read_bytes(),
                    receipt.read_bytes(),
                )
            assert len(source_ids) == len(TOPICS)
            assert len(provisioned) == execution.call_count == len(TOPICS)
            assert runs(endpoint) == before
            runtime.stop()
            # Recreate application/session/API objects as well as the daemon.
            runtime = ApplicationRuntime(work / "application")
            endpoint = runtime.start().base_url
            session = DesktopSession(runtime, threading.Event())
            session.connected(endpoint)
            api = DesktopAPI(
                session, lambda: cast("webview.Window", cast("object", None))
            )
            page.goto(endpoint + "/#help")
            for topic in TOPICS:
                page.locator(f'input[type="radio"][value="{topic}"]').press("Space")
                page.get_by_role(
                    "button", name=f"Continue {topic} Notebook", exact=True
                ).click()
                expect(
                    page.get_by_role(
                        "button", name=f"Continue {topic} Notebook", exact=True
                    )
                ).to_be_enabled()
                journey, notebook, code, receipt = retained[topic]
                assert current(runtime, topic) == journey
                assert journey.notebook.read_bytes() == notebook
                assert (
                    journey.directory / "src/my_experiment/setup.py"
                ).read_bytes() == code
                assert (runtime.home / f"learning/{topic}.json").read_bytes() == receipt
                assert opened[-1] == str(journey.notebook)
                page.screenshot(
                    path=str(work / f"{topic}-continue.png"), full_page=True
                )
            page.get_by_text("Repair Notebook environments", exact=True).click()
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(
                path=str(work / "course-browser-repair.png"), full_page=True
            )
            page.set_viewport_size({"width": 560, "height": 1000})
            courses.scroll_into_view_if_needed()
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(
                path=str(work / "course-browser-narrow.png"), full_page=True
            )
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            assert len(provisioned) == execution.call_count == len(TOPICS)
            assert len(opened) == 2 * len(TOPICS)
            assert runs(endpoint) == before
            browser.close()
        (work / "acceptance.json").write_text(
            json.dumps(
                {
                    "result": "passed",
                    "topics": TOPICS,
                    "real_chromium_help_desktop_api": "passed",
                    "same_application_distinct_registered_sources": len(source_ids),
                    "restart_continue_preserves_receipts_and_edits": "passed",
                    "start_and_continue_without_acquisition": "passed",
                    "continue_without_environment_preparation": "passed",
                    "substitutions": [
                        "development Python environment",
                        "external editor activation",
                    ],
                    "not_evaluated": [
                        "installation",
                        "native window bridge",
                        "external editor",
                        "Notebook kernels",
                        "unfamiliar user usability",
                    ],
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        runtime.stop()


if __name__ == "__main__":
    verify(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
