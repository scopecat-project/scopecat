"""Exercise shipped cells through Help, an independent real kernel and one app.

Usage: uv run --locked python scripts/verify_parameters_journey.py
       <fresh-work-directory> <toolchain-delivery>
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

import httpx2
from nbclient import NotebookClient
from nbformat import NotebookNode
from playwright.sync_api import expect, sync_playwright

from lab_tools.application_runtime import ApplicationRuntime
from lab_tools.desktop import DesktopAPI
from lab_tools.desktop_session import DesktopSession
from lab_tools.notebook import kernel_command
from lab_tools.notebook_io import notebook_io
from lab_tools.parameters_journey import current
from scopecat.daemon.views import RunSummary, RunSummaryPage

if TYPE_CHECKING:
    import webview


def verify(work: Path, payload: Path) -> None:
    # Daemons are detached grandchildren; cloud containers may have no reaping init.
    if sys.platform == "linux":
        if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER")
    nbformat = notebook_io()
    work.mkdir(parents=True, exist_ok=False)
    runtime = ApplicationRuntime(work / "application")
    runtime.configure(static_dir=payload / "gui", delivery_root=payload)
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
            expose("journeyStatus", api.parameters_journey)
            expose("journeyPrepare", api.prepare_parameters_journey)

            def editor() -> None:
                journey = current(runtime)
                assert journey is not None and journey.notebook.is_file()
                editor_files.append(str(journey.notebook))

            expose("journeyOpen", editor)
            page.add_init_script("""window.pywebview = {api: {
                parameters_journey: () => window.journeyStatus(),
                prepare_parameters_journey: (parent) =>
                    window.journeyPrepare(parent ?? null),
                open_parameters_notebook: () => window.journeyOpen(),
                set_window_title: async () => {},
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
                    },
                    indent=2,
                )
                + "\n"
            )
            browser.close()
    finally:
        runtime.stop()


if __name__ == "__main__":
    verify(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
