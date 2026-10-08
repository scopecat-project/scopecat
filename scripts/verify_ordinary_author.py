"""Settings → ordinary starter → explicit acquisition → exact result → restart.

Usage: uv run --locked python scripts/verify_ordinary_author.py WORK PAYLOAD [GUI]
PAYLOAD is a development delivery with lab_tools.toolchain added. No provisioning,
registration, parameters or setup are preseeded. Only native window/file-picker
plumbing and the external editor are substituted; cells use independent ipykernel.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

import httpx2
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError
from nbformat import NotebookNode
from playwright.sync_api import expect, sync_playwright

from lab_tools.application_runtime import ApplicationRuntime
from lab_tools.bundle import MANIFEST, file_hash
from lab_tools.desktop import DesktopAPI
from lab_tools.desktop_session import DesktopSession
from lab_tools.notebook import kernel_command
from lab_tools.notebook_io import notebook_io

if TYPE_CHECKING:
    import webview


class Window:
    """Substitute OS navigation/picker only; retain DesktopAPI product calls."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.url = ""

    def create_file_dialog(self, *_args: object) -> tuple[str]:
        return (str(self.directory),)

    def run_js(self, script: str) -> None:
        self.url = cast(
            "str",
            json.loads(
                script.removeprefix("window.location.replace(").removesuffix(");")
            ),
        )


def verify(work: Path, payload: Path, gui: Path) -> None:
    if sys.platform == "linux":
        if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER")
    work.mkdir(parents=True, exist_ok=False)
    runtime = ApplicationRuntime(work / "application")
    runtime.configure(static_dir=gui, delivery_root=payload)
    session = DesktopSession(runtime, threading.Event())
    window = Window(work)
    api = DesktopAPI(session, lambda: cast("webview.Window", cast("object", window)))
    evidence: dict[str, object] = {
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - fixed read-only tool
            text=True,
        ).strip(),
        "source_dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"],  # noqa: S607 - fixed read-only tool
                text=True,
            ).strip()
        ),
        "payload_manifest_sha256": file_hash(payload / MANIFEST),
    }
    nbformat = notebook_io()
    document = nbformat.v4.new_notebook(cells=[])
    cells = cast("list[NotebookNode]", document["cells"])
    try:
        endpoint = runtime.start().base_url
        session.connected(endpoint)

        def runs() -> list[dict[str, object]]:
            response = httpx2.get(endpoint + "/api/v1/runs", trust_env=False)
            response.raise_for_status()
            return cast("list[dict[str, object]]", response.json()["items"])

        with sync_playwright() as driver:
            browser = driver.chromium.launch(
                headless=True,
                args=["--no-sandbox"],
                executable_path=os.environ.get("SCOPECAT_TEST_CHROMIUM"),
            )
            page = browser.new_page()
            page.set_default_timeout(30_000)
            expose = cast(
                "Callable[[str, Callable[..., object]], object]", page.expose_function
            )
            expose("applicationStatus", lambda: api.status())
            expose("chooseDirectory", lambda: api.choose_directory())

            def create(parent: str, name: str) -> dict[str, str]:
                result = api.create_source(parent, name)
                return {"result": result, "url": window.url}

            expose("createSource", create)
            expose("selectEnvironment", api.select_source_environment)
            page.add_init_script("""window.pywebview = {api: {
                status: () => window.applicationStatus(),
                choose_directory: () => window.chooseDirectory(),
                create_source: async (parent, name) => {
                    const result = await window.createSource(parent, name);
                    window.location.replace(result.url);
                    return result.result;
                },
                select_source_environment: (source, python) =>
                    window.selectEnvironment(source, python),
                set_window_title: async () => {},
            }};""")
            page.goto(endpoint + "/#settings")
            page.get_by_role("button", name="New code folder", exact=True).click()
            page.get_by_role("button", name="Choose save location…", exact=True).click()
            # Error/retry preserves existing files and does not preseed author source.
            existing = work / "existing"
            existing.mkdir()
            (existing / "notes.txt").write_text("keep")
            page.get_by_label("New folder name", exact=True).fill("existing")
            create_button = page.get_by_role(
                "button", name="Create folder and prepare Python", exact=True
            )
            create_button.click()
            expect(page.get_by_role("alert")).to_contain_text("existing")
            assert (existing / "notes.txt").read_text() == "keep"
            assert runs() == []
            page.get_by_label("New folder name", exact=True).fill("my-experiments")
            create_button.click()
            source = work / "my-experiments"
            expect(page.get_by_label("Author directory", exact=True)).to_have_value(
                str(source), timeout=180_000
            )
            expect(page.get_by_text("Folder ready.", exact=False)).to_be_visible()
            bindings = cast("list[dict[str, str]]", api.status()["sources"])
            assert len(bindings) == 1
            binding = bindings[0]
            assert (
                binding["python"]
                != binding["execution_python"]
                != str(runtime.installation().python)
            )
            page.get_by_label("Execution Python", exact=True).fill(
                str(work / "missing-python")
            )
            page.get_by_role(
                "button", name="Use this execution Python", exact=True
            ).click()
            expect(page.get_by_role("alert")).to_be_visible()
            assert api.status()["sources"] == bindings
            page.get_by_label("Execution Python", exact=True).fill(
                binding["execution_python"]
            )
            page.get_by_role(
                "button", name="Use this execution Python", exact=True
            ).click()
            expect(page.get_by_role("alert")).to_have_count(0)
            assert runs() == []
            evidence["settings_preparation"] = {"passed": True, **binding}
            page.screenshot(path=str(work / "settings.png"), full_page=True)
            notebook = source / "notebooks/02_edit_scan.py"
            shipped = notebook.read_text()
            (work / "starter.py").write_text(shipped)
            parts = re.split(r"(?m)^# %%.*\n", shipped)
            assert len(parts) == 8, (
                "Starter combines preview, submission and results in one cell"
            )
            _, env = kernel_command(
                source,
                python=binding["python"],
                source_path=False,
                kernel_home=work / "kernel",
            )
            os.environ["JUPYTER_PATH"] = env["JUPYTER_PATH"]
            client = NotebookClient(
                document,
                kernel_name="scopecat-lab",
                timeout=120,
                resources={"metadata": {"path": str(source / "notebooks")}},
            )

            def execute(code: str) -> None:
                cell = nbformat.v4.new_code_cell(code)
                cells.append(cell)
                client.execute_cell(cell, len(cells) - 1)

            with client.setup_kernel():
                # VS Code's Python-file interactive entry supplies __file__.
                execute(f"__file__ = {str(notebook)!r}")
                execute(parts[0])
                execute(parts[1])
                execute(parts[2])
                execute(parts[3])
                assert runs() == []
                code = source / "src/scopecat_lab/authored/signal.py"
                original = code.read_text()
                code.write_text(original + "\ninvalid Python (\n")
                try:
                    execute(parts[3])
                except CellExecutionError:
                    evidence["invalid_source_preview"] = "rejected without acquisition"
                else:
                    raise AssertionError("Invalid source accepted")
                assert runs() == []
                code.write_text(
                    original.replace("return scale /", "return 2 * scale /")
                )
                execute(parts[3])
                assert runs() == []
                for part in parts[4:]:
                    execute(part)
                assert len(runs()) == 1
                execute(
                    "assert list(values) == [1.0, 2.0, 1.0]\n"
                    f"Path({str(work / 'receipt.txt')!r}).write_text(str(job.receipt))"
                )
            first = runs()[0]
            run_id = str(
                cast(
                    "dict[str, object]",
                    cast("dict[str, object]", first["control"])["admission"],
                )["run_id"]
            )
            page.get_by_role("button", name="Runs", exact=True).click()
            page.get_by_title(f"Inspect run {run_id}", exact=True).click()
            expect(page.get_by_test_id("run-status")).to_have_text("Succeeded")
            expect(page.get_by_title(run_id, exact=True)).to_be_visible()
            table = page.get_by_test_id("measurement-table")
            expect(table.locator("tbody tr")).to_have_count(3)
            assert table.locator("tbody tr").evaluate_all(
                "rows => rows.map(row => row.lastElementChild.textContent)"
            ) == ["1", "2", "1"]
            evidence["gui_exact_values"] = [1, 2, 1]
            page.screenshot(path=str(work / "exact-run.png"), full_page=True)
            evidence["run_id"] = run_id
            evidence["values"] = [1.0, 2.0, 1.0]
            before = runs()
            runtime.stop()
            runtime = ApplicationRuntime(work / "application")
            endpoint = runtime.start().base_url
            session = DesktopSession(runtime, threading.Event())
            session.connected(endpoint)
            api = DesktopAPI(
                session, lambda: cast("webview.Window", cast("object", window))
            )
            page.goto(endpoint + "/#settings")
            page.get_by_label("Your code folders").select_option(str(source))
            expect(page.get_by_label("Author directory")).to_have_value(str(source))
            receipt = (work / "receipt.txt").read_text()
            with client.setup_kernel():
                execute(f"__file__ = {str(notebook)!r}")
                execute(parts[0])
                execute(parts[1])
                execute(parts[2])
                execute(f"receipt = Path({receipt!r})")
                execute(parts[-2])
                execute("assert list(values) == [1.0, 2.0, 1.0]")
                execute(parts[-1])
            assert runs() == before
            assert code.read_text() == original.replace(
                "return scale /", "return 2 * scale /"
            )
            page.get_by_role("button", name="Runs", exact=True).click()
            page.get_by_title(f"Inspect run {run_id}", exact=True).click()
            expect(page.get_by_test_id("run-status")).to_have_text("Succeeded")
            expect(table.locator("tbody tr")).to_have_count(3)
            assert table.locator("tbody tr").evaluate_all(
                "rows => rows.map(row => row.lastElementChild.textContent)"
            ) == ["1", "2", "1"]
            evidence["restart_reopen_without_acquisition"] = "passed"
            evidence["substitutions"] = [
                "native window and folder picker",
                "VS Code activation and __file__ injection",
            ]
            evidence["result"] = "passed"
            browser.close()
    finally:
        runtime.stop()
        nbformat.write(document, work / "executed-cells.ipynb")
        (work / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    verify(
        Path(sys.argv[1]).resolve(),
        Path(sys.argv[2]).resolve(),
        Path(sys.argv[3]).resolve()
        if len(sys.argv) > 3
        else Path(sys.argv[2]).resolve() / "gui",
    )
