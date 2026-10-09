"""Exercise shipped cells through Help, an independent real kernel and one app.

Usage: uv run --locked python scripts/verify_notebook_journey.py
       <fresh-work-directory> <toolchain-delivery> [current-gui-dist]
Uses development dependencies and Playwright Chromium (or SCOPECAT_TEST_CHROMIUM).
Browser-native bridge calls use
DesktopAPI; only external editor activation/window plumbing is substituted.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import threading
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import httpx2
from nbclient import NotebookClient
from nbformat import NotebookNode
from playwright.sync_api import Page, expect, sync_playwright

from lab_teaching.lessons import LessonTopic
from lab_tools.application_runtime import ApplicationRuntime
from lab_tools.desktop import DesktopAPI
from lab_tools.desktop_session import DesktopSession
from lab_tools.notebook import kernel_command
from lab_tools.notebook_io import notebook_io
from lab_tools.notebook_journey import NotebookJourney, current
from lab_tools.verify_groups import GROUP_CHECKS, GROUP_REOPEN_CELLS
from scopecat.daemon.views import RunSummary, RunSummaryPage

if TYPE_CHECKING:
    import webview


def verify(work: Path, payload: Path, gui: Path | None = None) -> None:
    # Daemons are detached grandchildren; cloud containers may have no reaping init.
    if sys.platform == "linux":
        if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER")
    nbformat = notebook_io()
    work.mkdir(parents=True, exist_ok=False)
    runtime = ApplicationRuntime(work / "application")
    runtime.configure(static_dir=gui or payload / "gui", delivery_root=payload)
    session = DesktopSession(runtime, threading.Event())
    api = DesktopAPI(session, lambda: cast("webview.Window", cast("object", None)))
    editor_files: list[str] = []
    try:
        endpoint = runtime.start().base_url
        session.connected(endpoint)
        with sync_playwright() as browser_driver:
            browser = browser_driver.chromium.launch(
                headless=True,
                args=["--no-sandbox"],
                executable_path=os.environ.get("SCOPECAT_TEST_CHROMIUM"),
            )
            page = browser.new_page()
            page.set_default_timeout(180_000)
            expose = cast(
                "Callable[[str, Callable[..., object]], object]",
                page.expose_function,
            )
            expose("journeyStatus", api.notebook_journey)
            expose("journeyPrepare", api.prepare_notebook_journey)
            expose("applicationStatus", api.status)

            def editor(topic: str) -> None:
                def record(journey: NotebookJourney) -> None:
                    assert journey.notebook.is_file()
                    editor_files.append(str(journey.notebook))

                with patch("lab_tools.notebook_journey.open_editor", record):
                    api.open_lesson_notebook(cast("LessonTopic", topic))

            expose("journeyOpen", editor)
            page.add_init_script("""window.pywebview = {api: {
                notebook_journey: (topic) => window.journeyStatus(topic),
                prepare_notebook_journey: (parent, topic, repair = false) =>
                    window.journeyPrepare(parent ?? null, topic, repair),
                open_lesson_notebook: (topic) => window.journeyOpen(topic),
                set_window_title: async () => {},
                status: () => window.applicationStatus(),
            }};""")
            page.goto(endpoint + "/#help")
            page.get_by_role(
                "button", name="Start parameters Notebook", exact=True
            ).click()
            expect(
                page.get_by_role(
                    "button", name="Continue parameters Notebook", exact=True
                )
            ).to_be_enabled()
            journey = current(runtime)
            assert journey is not None and journey.ready
            assert editor_files == [str(journey.notebook)]
            expect(page.get_by_text(str(journey.notebook), exact=True)).to_be_visible()
            page.evaluate("window.continuationDocument = true")
            page.get_by_role(
                "link", name="Manage this code folder in Settings", exact=True
            ).click()
            expect(page.get_by_label("Your code folders")).to_have_value(
                str(journey.directory)
            )
            expect(page.get_by_label("Author directory")).to_have_value(
                str(journey.directory)
            )
            assert page.evaluate("window.continuationDocument") is True
            page.get_by_role("button", name="Help", exact=True).click()
            expect(page.get_by_role("combobox", name="Course")).to_have_value(
                "parameters"
            )
            assert editor_files == [str(journey.notebook)]
            source = journey.directory
            notebook_bytes = journey.notebook.read_bytes()
            material = (
                Path(__file__).resolve().parents[1]
                / "packages/lab-teaching/src/lab_teaching/course_material/lessons"
            )
            for packaged, generated in (
                ("parameters.ipynb", "notebooks/parameters.ipynb"),
                ("parameters_setup.py.txt", "src/my_experiment/setup.py"),
                ("parameters.py.txt", "src/my_experiment/parameters.py"),
                ("response.py.txt", "src/my_experiment/response.py"),
                ("experiment.py.txt", "src/my_experiment/teaching.py"),
                ("workspace_app.py.txt", "src/workspace_app.py"),
            ):
                assert (source / generated).read_bytes() == (
                    material / packaged
                ).read_bytes(), (
                    "Installed teaching material differs from this checkout; run "
                    "uv sync --locked --reinstall-package scopecat-lab-teaching"
                )
            notebook_sha256 = hashlib.sha256(notebook_bytes).hexdigest()
            shipped = nbformat.read(journey.notebook, as_version=4)
            shipped_cells = cast("list[NotebookNode]", shipped["cells"])
            cells = {cast("str", cell["id"]): cell for cell in shipped_cells}
            executed_cells: list[NotebookNode] = []
            evidence = nbformat.v4.new_notebook(cells=executed_cells)
            executed_cells = cast("list[NotebookNode]", evidence["cells"])
            python = Path(str(journey.view()["python"]))
            _, environment = kernel_command(
                source,
                python=str(python),
                source_path=False,
                kernel_home=work / "kernel",
            )
            # Expose only the generated kernelspec to this verification process.
            os.environ["JUPYTER_PATH"] = environment["JUPYTER_PATH"]
            client = NotebookClient(
                evidence,
                kernel_name="scopecat-lab",
                timeout=120,
                resources={"metadata": {"path": str(source / "notebooks")}},
            )

            def execute(identifier: str) -> None:
                cell = deepcopy(cells[identifier])
                cell["id"] = f"{identifier}-execution-{len(executed_cells)}"
                executed_cells.append(cell)
                client.execute_cell(cell, len(executed_cells) - 1)

            def check(code: str) -> None:
                cell = nbformat.v4.new_code_cell(code)
                executed_cells.append(cell)
                client.execute_cell(cell, len(executed_cells) - 1)

            def runs() -> tuple[RunSummary, ...]:
                response = httpx2.get(endpoint + "/api/v1/runs", trust_env=False)
                response.raise_for_status()
                return RunSummaryPage.model_validate_json(response.content).items

            assert runs() == ()
            with client.setup_kernel():
                for cell in shipped_cells:
                    if cell["cell_type"] == "code":
                        execute(cast("str", cell["id"]))
                check(
                    "assert shots.shape == (7, 64)\nfirst_id = run.id\n"
                    "params[Drive]['q0'].frequency = 5.152\n"
                    "params.save(note='retained choice')"
                )
                first_id = runs()[0].run_id
                first_runs = runs()
                execute("parameters-result")
                assert runs() == first_runs
                page.goto(endpoint + "/?run=" + first_id)
                expect(page.get_by_text(first_id, exact=True)).to_be_visible()
                code = source / "src/my_experiment/teaching.py"
                code.write_text(
                    code.read_text().replace("shots: int = 64", "shots: int = 32")
                )
                execute("parameters-4")
                execute("parameters-6")
                execute("parameters-result")
                check(
                    "assert shots.shape == (7, 32)\n"
                    "old = session.run(first_id).measurements()['iq']\n"
                    "assert np.asarray(old.require_values()).shape == (7, 64)"
                )
                cells["parameters-4"]["source"] = (
                    cast("str", cells["parameters-4"]["source"])
                    .replace("0.8, 7", "0.8, 5")
                    .replace("== 7", "== 5")
                )
                nbformat.write(shipped, journey.notebook)
                notebook_bytes = journey.notebook.read_bytes()
                execute("parameters-4")
                execute("parameters-6")
                execute("parameters-result")
                check(
                    "assert shots.shape == (5, 32)\n"
                    "assert params[Drive]['q0'].frequency == 5.152\n"
                    "session.close()"
                )
            before = runs()
            assert len(before) == 3
            runtime.stop()
            endpoint = runtime.start().base_url
            session.connected(endpoint)
            page.goto(endpoint + "/#help")
            page.get_by_role(
                "button", name="Continue parameters Notebook", exact=True
            ).click()
            expect(
                page.get_by_role(
                    "button", name="Continue parameters Notebook", exact=True
                )
            ).to_be_enabled()
            assert current(runtime) == journey
            assert journey.notebook.read_bytes() == notebook_bytes
            assert "shots: int = 32" in code.read_text()
            with client.setup_kernel():
                execute("parameters-1")
                execute("parameters-2")
                check(
                    "from my_experiment.parameters import Drive\n"
                    "assert params[Drive]['q0'].frequency == 5.152\n"
                    f"run = session.run({first_id!r})"
                )
                execute("parameters-result")
                check("assert shots.shape == (7, 64)\nsession.close()")
            assert runs() == before
            page.get_by_role("link", name="Runs", exact=True).click()
            expect(
                page.get_by_role("button", name="Runs", exact=True)
            ).to_have_attribute("aria-current", "page")
            page.get_by_title(f"Inspect run {first_id}", exact=True).click()
            expect(page.get_by_text(first_id, exact=True)).to_be_visible()
            assert runs() == before
            page.screenshot(path=str(work / "same-run.png"), full_page=True)
            nbformat.write(evidence, work / "executed-cells.ipynb")
            (work / "acceptance.json").write_text(
                json.dumps(
                    {
                        "result": "passed",
                        "shipped_notebook_sha256": notebook_sha256,
                        "first_run": first_id,
                        "shapes": [[7, 64], [7, 32], [5, 32]],
                        "retained_runs": len(before),
                        "source": str(source),
                        "kernel": str(python),
                        "native_editor": "not evaluated",
                        "browser_help_and_same_run": "passed",
                        "restart_and_continue_without_acquisition": "passed",
                        "repeat_result_cell_without_acquisition": "passed",
                        "help_runs_link_reopens_retained_run": "passed",
                        "settings_same_source_without_reload": "passed",
                    },
                    indent=2,
                )
                + "\n"
            )
            verify_groups(page, runtime, session, work, endpoint)
            groups = current(runtime, "groups")
            assert groups is not None
            assert editor_files == [
                str(journey.notebook),
                str(journey.notebook),
                str(groups.notebook),
                str(groups.notebook),
            ]
            os.environ["JUPYTER_PATH"] = environment["JUPYTER_PATH"]
            assert current(runtime) == journey
            assert journey.notebook.read_bytes() == notebook_bytes
            with client.setup_kernel():
                execute("parameters-1")
                execute("parameters-2")
                check(
                    "from my_experiment.parameters import Drive\n"
                    "assert params[Drive]['q0'].frequency == 5.152\n"
                    "session.close()"
                )
            browser.close()
    finally:
        runtime.stop()


def verify_groups(
    page: Page,
    runtime: ApplicationRuntime,
    desktop: DesktopSession,
    work: Path,
    endpoint: str,
) -> None:
    """Verify a second course with independent inputs in the same application."""
    nbformat = notebook_io()
    page.goto(endpoint + "/#help")
    page.get_by_role("combobox", name="Course").select_option("groups")
    page.get_by_role("button", name="Start groups Notebook", exact=True).click()
    expect(
        page.get_by_role("button", name="Continue groups Notebook", exact=True)
    ).to_be_enabled()
    journey = current(runtime, "groups")
    parameters = current(runtime)
    assert journey is not None and parameters is not None and journey.ready
    source = journey.directory
    identity = Path("src/my_experiment/lesson_identity.py")
    assert (source / identity).read_bytes() != (
        parameters.directory / identity
    ).read_bytes()
    material = (
        Path(__file__).resolve().parents[1]
        / "packages/lab-teaching/src/lab_teaching/course_material"
    )
    for generated, packaged in (
        ("notebooks/groups.ipynb", "lessons/groups.ipynb"),
        ("src/my_experiment/setup.py", "lessons/parameters_setup.py.txt"),
        ("src/my_experiment/teaching.py", "lessons/experiment.py.txt"),
        ("src/my_experiment/group_analysis.py", "group_analysis.py"),
    ):
        assert (source / generated).read_bytes() == (material / packaged).read_bytes()
    shipped = nbformat.read(journey.notebook, as_version=4)
    material_hash = hashlib.sha256(journey.notebook.read_bytes()).hexdigest()
    evidence = deepcopy(shipped)
    cast("list[NotebookNode]", evidence["cells"]).append(
        nbformat.v4.new_code_cell(GROUP_CHECKS)
    )
    python = str(journey.view()["python"])
    _, environment = kernel_command(
        source, python=python, source_path=False, kernel_home=work / "groups-kernel"
    )
    os.environ["JUPYTER_PATH"] = environment["JUPYTER_PATH"]

    def run_notebook(document: NotebookNode) -> None:
        NotebookClient(
            document,
            kernel_name="scopecat-lab",
            timeout=120,
            resources={"metadata": {"path": str(source / "notebooks")}},
        ).execute()

    run_notebook(evidence)
    nbformat.write(evidence, work / "groups-executed.ipynb")
    code = source / "src/my_experiment/teaching.py"
    code.write_text(code.read_text().replace("shots: int = 64", "shots: int = 32"))
    cast("list[NotebookNode]", shipped["cells"]).append(
        nbformat.v4.new_code_cell("# My retained grouping notes")
    )
    nbformat.write(shipped, journey.notebook)
    retained = journey.notebook.read_bytes()
    response = httpx2.get(endpoint + "/api/v1/runs", trust_env=False)
    response.raise_for_status()
    before = RunSummaryPage.model_validate_json(response.content).items
    assert len(before) == 5  # Three parameters runs and two groups runs.
    runtime.stop()
    endpoint = runtime.start().base_url
    desktop.connected(endpoint)
    page.goto(endpoint + "/#help")
    page.get_by_role("combobox", name="Course").select_option("groups")
    page.get_by_role("button", name="Continue groups Notebook", exact=True).click()
    expect(
        page.get_by_role("button", name="Continue groups Notebook", exact=True)
    ).to_be_enabled()
    assert current(runtime, "groups") == journey
    page.get_by_role("link", name="Runs", exact=True).click()
    page.get_by_role("button", name="Help", exact=True).click()
    expect(page.get_by_role("combobox", name="Course")).to_have_value("groups")
    page.reload()
    expect(page.get_by_role("combobox", name="Course")).to_have_value("groups")
    expect(
        page.get_by_role("button", name="Continue groups Notebook", exact=True)
    ).to_be_enabled()
    page.screenshot(path=str(work / "groups-help.png"), full_page=True)
    assert journey.notebook.read_bytes() == retained
    assert "shots: int = 32" in code.read_text()
    reopened = nbformat.v4.new_notebook(
        cells=[nbformat.v4.new_code_cell(c) for c in GROUP_REOPEN_CELLS]
    )
    run_notebook(reopened)
    nbformat.write(reopened, work / "groups-reopened.ipynb")
    response = httpx2.get(endpoint + "/api/v1/runs", trust_env=False)
    response.raise_for_status()
    assert RunSummaryPage.model_validate_json(response.content).items == before
    bookmark = cast(
        "dict[str, str]", json.loads((source / "grouped-run.json").read_text())
    )
    page.get_by_role("link", name="Runs", exact=True).click()
    page.get_by_title(f"Inspect run {bookmark['run_id']}", exact=True).click()
    run_label = page.get_by_text(bookmark["run_id"], exact=True)
    expect(run_label).to_be_visible()
    selected_run = run_label.inner_text()
    summaries = page.get_by_test_id("resource-card").locator("details > summary")
    expect(summaries.first).to_be_visible()
    for summary in summaries.all():
        summary.click()
    publication = page.get_by_test_id("publication-id").filter(
        has_text=bookmark["publication_id"]
    )
    expect(publication).to_have_text(bookmark["publication_id"])
    selected_publication = publication.inner_text()
    # Use the shipped learner's read-only example, with identities taken from UI.
    # The bookmark above selects the expected attempt, not the kernel readback.
    notes = [
        cell
        for cell in cast("list[NotebookNode]", shipped["cells"])
        if cell["id"] == "groups-5"
    ]
    assert len(notes) == 1
    example = (
        cast("str", notes[0]["source"]).split("```python\n", 1)[1].split("```", 1)[0]
    )
    example = example.replace("此前的运行 ID", selected_run).replace(
        "此前的分组分析 ID", selected_publication
    )
    ui_reopen = nbformat.v4.new_notebook(
        cells=[
            nbformat.v4.new_code_cell("import scopecat as sc\nsession = sc.notebook()"),
            nbformat.v4.new_code_cell(
                f"before_runs = session.list_runs()\n"
                f"before_analyses = session.run({selected_run!r}).analysis_summaries()"
            ),
            nbformat.v4.new_code_cell(example),
            nbformat.v4.new_code_cell(
                "assert len(restored.groups) == 2\n"
                "assert len(run.measurements()) == 42\n"
                "assert run.analysis_summaries() == before_analyses\n"
                "assert session.list_runs() == before_runs\n"
                "session.close()"
            ),
        ]
    )
    run_notebook(ui_reopen)
    nbformat.write(ui_reopen, work / "groups-ui-reopened.ipynb")
    page.screenshot(path=str(work / "groups-same-run.png"), full_page=True)
    (work / "groups-acceptance.json").write_text(
        json.dumps(
            {
                "result": "passed",
                "shipped_notebook_sha256": material_hash,
                "retained_application_runs": len(before),
                "points": [42, 63],
                "groups": [2, 3],
                "restart_continue_and_read_without_acquisition": "passed",
                "source_and_notebook_edits_preserved": "passed",
                "parameters_and_groups_independent": "passed",
                "ui_ids_shipped_example_fresh_kernel_without_new_work": "passed",
                "native_editor": "not evaluated",
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    verify(
        Path(sys.argv[1]).resolve(),
        Path(sys.argv[2]).resolve(),
        Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else None,
    )
