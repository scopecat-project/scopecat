"""Bounded native WebView/bridge acceptance, using the headless check's fixtures.

Run only from verify_native_application against its disposable application/home.
The probe copies the native host and replaces only its bootstrap. The production
WebView backend/store is used unchanged, including any production dependency
repair. Execution requires a disposable GitHub-hosted Mac/Windows runner.
It is not production startup, focus, menu, tray or human-interaction qualification.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

import httpx2
import psutil
from pydantic import BaseModel, ConfigDict

from lab_tools.application_runtime import ApplicationRuntime
from lab_tools.bundle import file_hash
from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.automation import (
    InterpretationRequest,
    ProcedureStepAttemptListQuery,
    procedure,
)
from scopecat.automation.worker import ProcedureContext, ProcedureWorker
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.views import RunDetail, RunSummaryPage
from scopecat_server.validation_process import (  # noqa: TID251 - acceptance owns its processes
    terminate_validation_process_tree,
)

if TYPE_CHECKING:
    import webview


# The same first-ingest gate as the real browser journey. Calls delegate to the
# real SDK/service; no Window, bridge, daemon or measurement response is mocked.
ACQUISITION = """
import sys, time
from pathlib import Path
import scopecat as sc
root, gates = map(Path, sys.argv[1:])

@sc.experiment(id="native_window_scan")
def scan(experiment: sc.ExperimentContext) -> sc.CoordinateRef[int]:
    return experiment.scan("value", tuple(range(15)))

with sc.open_project(root).connect() as lab:
    prepared = lab.prepare(scan.build(), config=lab.parameters.resolve(
        lab.parameters.get("starter-initial"), setup=lab.setup.get("starter-bench")))
    original = lab._client.ingest_measurements
    count = 0
    def ingest(run_id, *, lease_id, append, dataset_schema):
        global count
        receipt = original(run_id, lease_id=lease_id, append=append,
                           dataset_schema=dataset_schema)
        count += 1
        (gates / "ingests.json").write_text(str(count))
        if count == 1:
            (gates / "ready").write_text(run_id)
            deadline = time.monotonic() + 150
            while not (gates / "release").exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("Native probe did not release acquisition")
                time.sleep(0.05)
        return receipt
    lab._client.ingest_measurements = ingest
    try:
        run = prepared.run(name="Native window acceptance")
    finally:
        lab._client.ingest_measurements = original
    (gates / "completed").write_text(run.id)
"""


class Review(BaseModel):
    model_config = ConfigDict(frozen=True)

    answer: str


REVIEW_SCHEMA = AnalysisFactSchema("native.window.review.v1", Review)


@procedure(id="native.window.review", version="1", intent=Review)
def review(context: ProcedureContext, intent: Review) -> None:
    context.interpret(
        "review",
        request=InterpretationRequest(
            title="Native window draft acceptance",
            instructions="Keep this review as an unsubmitted draft.",
            schema_id=REVIEW_SCHEMA.id,
            schema_hash=REVIEW_SCHEMA.schema_hash,
            structure=REVIEW_SCHEMA.structure,
            response_template={"answer": intent.answer},
        ),
    )


def prepare_decision(base_url: str) -> dict[str, str | int]:
    """Create a real waiting interpretation, without starting another acquisition."""
    with DaemonClient(base_url) as client:
        waiting = ProcedureWorker(client).execute(
            review, {"answer": "pending"}, "native-window-review", "native-window-probe"
        )
        assert waiting.state == "waiting_for_input"
        page = client.list_procedure_step_attempts(
            waiting.procedure_run_id, ProcedureStepAttemptListQuery()
        )
        assert len(page.items) == 1 and page.next_cursor is None
        step = page.items[0]
        assert step.state == "waiting_for_input"
        return {
            "procedure_run_id": waiting.procedure_run_id,
            "step_key": step.step_key,
            "attempt": step.attempt,
        }


def read_decision_draft(
    base_url: str, target: dict[str, str | int]
) -> dict[str, object]:
    """Read the application-owned record; require the combined draft API candidate."""
    with httpx2.Client(base_url=base_url, trust_env=False, timeout=5) as http:
        response = http.post("/api/v1/decision-drafts/read", json=target)
        response.raise_for_status()
        return cast("dict[str, object]", response.json())


def draft_actor(view: dict[str, object]) -> object:
    draft = cast("dict[str, object] | None", view["draft"])
    return None if draft is None else cast("dict[str, object]", draft["input"])["actor"]


def wait_for(check: Callable[[], bool], message: str, *, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() >= deadline:
            raise TimeoutError(message)
        time.sleep(0.05)


def run_bounded(command: list[str], log: Path, *, timeout: float) -> None:
    with log.open("a", encoding="utf-8") as stream:
        process = subprocess.Popen(  # noqa: S603 - fixed packaged probe/cleanup
            command, stdout=stream, stderr=stream, text=True, env=environment()
        )
        owner = psutil.Process(process.pid)
        try:
            code = process.wait(timeout=timeout)
            if code:
                raise subprocess.CalledProcessError(code, command)
        finally:
            if process.poll() is None:
                terminate_validation_process_tree(process, owner=owner)


def environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key not in ("SCOPECAT_DAEMON_URL", "PYTHONHOME", "PYTHONPATH")
    }


def require_hosted_runner(home: Path) -> None:
    """Refuse accidental local execution; environment checks are not attestation.

    --home/storage_path cannot isolate Cocoa's default WebKit store. There is no
    supported local override: use the manual acceptance job on a disposable VM.
    """
    runner_os = {"darwin": "macOS", "win32": "Windows"}.get(sys.platform)
    temporary = os.environ.get("RUNNER_TEMP", "")
    if (
        runner_os is None
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("RUNNER_OS") != runner_os
        or not temporary
        or not Path(temporary).is_absolute()
        or not home.resolve().is_relative_to(Path(temporary).resolve())
        or home.resolve() == Path(temporary).resolve()
    ):
        raise RuntimeError(
            "Native window acceptance requires a disposable GitHub-hosted "
            "Mac/Windows runner and a home below RUNNER_TEMP. Do not run on a "
            "user machine: production WebKit may clear its default website store."
        )


def cleanup(home: Path) -> None:
    """Recover a gated client even after its native parent exited unexpectedly."""
    receipt = home / "native-windows/acquisition-process.json"
    try:
        if receipt.exists():
            identity = cast("dict[str, int | float]", json.loads(receipt.read_bytes()))
            try:
                process = psutil.Process(int(identity["pid"]))
                if process.create_time() == identity["created"]:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except psutil.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass
    finally:
        ApplicationRuntime(home / "data").stop()


def evaluate(window: webview.Window, script: str) -> object:
    return cast("object", window.evaluate_js(script))


def text_content(window: webview.Window, test_id: str) -> str:
    return str(
        evaluate(
            window,
            f"document.querySelector('[data-testid={test_id}]')?.textContent || ''",
        )
    )


def click_text(window: webview.Window, label: str) -> None:
    assert (
        evaluate(
            window,
            "(() => { const element = [...document.querySelectorAll('button, summary')]"
            f".find(e => e.textContent.trim() === {json.dumps(label)});"
            "if (!element || element.disabled) return false; "
            "element.click(); return true; })()",
        )
        is True
    )


def probe(home: Path) -> None:
    require_hosted_runner(home)
    import webview

    from lab_tools.desktop import DesktopWindows
    from lab_tools.desktop_session import DesktopSession

    reports = home / "native-windows"
    runtime = ApplicationRuntime(home / "data")
    closing = threading.Event()
    session = DesktopSession(runtime, closing)
    windows = DesktopWindows(session, lambda: None)
    evidence: dict[str, object] = {
        "status": "failed",
        "platform": platform.platform(),
        "python": sys.version,
        "checks": [],
        "not_evaluated": [
            "OS focus",
            "OS menu clicks",
            "tray",
            "production startup",
            "human presentation",
        ],
    }
    checks: list[str] = []
    evidence["checks"] = checks
    child: subprocess.Popen[str] | None = None
    child_owner: psutil.Process | None = None
    try:
        if sys.platform == "darwin":
            from lab_tools.desktop_platform import macos_bundle_identifier

            assert macos_bundle_identifier() == "org.scopecat.desktop"
        record = runtime.start()
        session.connected(record.base_url)
        draft_target = prepare_decision(record.base_url)
        evidence["draft_target"] = draft_target
        assert read_decision_draft(record.base_url, draft_target)["draft"] is None
        evidence["service"] = {
            "pid": record.pid,
            "base_url": record.base_url,
            "process_create_time": record.process_create_time,
        }
        script = reports / "acquisition.py"
        script.write_text(ACQUISITION, encoding="utf-8")
        client = (
            home
            / "authors/.venv"
            / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )
        retained = cast(
            "dict[str, str]", json.loads((home / "authors/saved-run.json").read_bytes())
        )["run_id"]
        with (reports / "acquisition.log").open("wb") as log:
            child = subprocess.Popen(  # noqa: S603 - real isolated SDK fixture
                [
                    str(client),
                    "-I",
                    "-B",
                    str(script),
                    str(home / "authors"),
                    str(reports),
                ],
                stdout=log,
                stderr=log,
                text=True,
                env=environment(),
            )
        child_owner = psutil.Process(child.pid)
        (reports / "acquisition-process.json").write_text(
            json.dumps(
                {
                    "pid": child.pid,
                    "created": child_owner.create_time(),
                }
            ),
            encoding="utf-8",
        )
        wait_for(
            lambda: (reports / "ready").exists() or child.poll() is not None,
            "Acquisition never reached its first real ingest",
        )
        assert child.poll() is None, "Acquisition exited early; see acquisition.log"
        run_id = (reports / "ready").read_text()
        evidence["run_id"] = run_id
        first = windows.create(run_id)

        def exercise() -> None:
            try:
                with httpx2.Client(
                    base_url=record.base_url, trust_env=False, timeout=5
                ) as http:

                    def runs() -> set[str]:
                        response = http.get("/api/v1/runs", params={"limit": 100})
                        response.raise_for_status()
                        page = RunSummaryPage.model_validate(response.json())
                        assert page.next_cursor is None
                        return {item.run_id for item in page.items}

                    def detail() -> RunDetail:
                        response = http.get(f"/api/v1/runs/{run_id}")
                        response.raise_for_status()
                        return RunDetail.model_validate(response.json())

                    baseline = runs()
                    initial = detail()
                    assert (
                        initial.control.state == "leased"
                        and initial.snapshot.outcome is None
                    )
                    wait_for(
                        lambda: (
                            run_id in text_content(first.window, "run-detail-header")
                        ),
                        "Main run did not render",
                    )
                    wait_for(
                        lambda: (
                            evaluate(
                                first.window,
                                "typeof window.pywebview?.api?.open_run_window "
                                "=== 'function'",
                            )
                            is True
                        ),
                        "Native bridge did not become ready",
                    )
                    # Generate the draft through the real React form, then unmount
                    # it so its effect cannot rewrite a cleared store during creation.
                    reviewer = "native-window-reviewer"
                    reviewer_selector = (
                        'input[placeholder="name, agent id, or service id"]'
                    )
                    reviewer_value = (
                        "document.querySelector("
                        + json.dumps(reviewer_selector)
                        + ")?.value"
                    )
                    click_text(first.window, "Decisions")
                    wait_for(
                        lambda: evaluate(first.window, reviewer_value) == "",
                        "Real waiting decision form did not render",
                    )
                    evaluate(
                        first.window,
                        "(() => { const e = document.querySelector("
                        + json.dumps(reviewer_selector)
                        + ");"
                        "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"
                        "'value').set.call(e, " + json.dumps(reviewer) + ");"
                        "e.dispatchEvent(new Event('input', {bubbles: true})); })()",
                    )

                    def draft_read() -> dict[str, object]:
                        return read_decision_draft(record.base_url, draft_target)

                    wait_for(
                        lambda: draft_actor(draft_read()) == reviewer,
                        "Editing the decision did not save an application-owned draft",
                    )
                    draft_before = draft_read()
                    assert draft_before["validity"] == "current"
                    marker_key = "scopecat:native-window-storage-marker"
                    marker_read = "localStorage.getItem(" + json.dumps(marker_key) + ")"
                    evaluate(
                        first.window,
                        "localStorage.setItem("
                        + json.dumps(marker_key)
                        + ", 'before-secondary')",
                    )
                    click_text(first.window, "Runs")
                    wait_for(
                        lambda: (
                            run_id in text_content(first.window, "run-detail-header")
                        ),
                        "Main run did not return after saving draft",
                    )
                    checks.append("real decision edit saved before secondary creation")
                    click_text(first.window, "Open result in new window")
                    wait_for(
                        lambda: len(webview.windows) == 2,
                        "Bridge did not create a native window",
                    )
                    second = windows.latest
                    assert second is not first
                    wait_for(
                        lambda: (
                            run_id in text_content(second.window, "run-detail-header")
                        ),
                        "Exact run did not render in secondary window",
                    )
                    storage_evidence: dict[str, object] = {
                        "draft_before": draft_before,
                        "marker_before": "before-secondary",
                    }
                    evidence["storage"] = storage_evidence

                    def check_storage(stage: str) -> None:
                        observed = {
                            "draft": draft_read(),
                            "marker": evaluate(first.window, marker_read),
                        }
                        storage_evidence[stage] = observed
                        assert observed == {
                            "draft": draft_before,
                            "marker": "before-secondary",
                        }, f"Production store changed at {stage}: {observed!r}"

                    check_storage("after_secondary_creation")
                    # Observe the second window's storage without imposing a new
                    # cross-window sharing contract on the production backend.
                    storage_evidence["secondary"] = {
                        "application_draft": draft_read(),
                        "marker": evaluate(second.window, marker_read),
                    }
                    click_text(first.window, "Decisions")
                    wait_for(
                        lambda: evaluate(first.window, reviewer_value) == reviewer,
                        "Real decision draft did not restore after secondary creation",
                    )
                    click_text(first.window, "Runs")
                    wait_for(
                        lambda: (
                            run_id in text_content(first.window, "run-detail-header")
                        ),
                        "Main run did not return after draft restoration",
                    )
                    check_storage("after_draft_restoration")
                    checks.append(
                        "native store retains marker; application restores draft"
                    )
                    # Exercise both actual injected bridges. Window.title tracks
                    # the Python target; this does not qualify OS title rendering.
                    for source, other, title in (
                        (first, second, "native-probe-main"),
                        (second, first, "native-probe-secondary"),
                    ):
                        original_title, other_title = (
                            source.window.title,
                            other.window.title,
                        )
                        evaluate(
                            source.window,
                            "void window.pywebview.api.set_window_title("
                            + json.dumps(title)
                            + ")",
                        )
                        wait_for(
                            lambda source=source, title=title: (
                                source.window.title == title
                            ),
                            "Bridge title call did not reach its owning window",
                        )
                        assert other.window.title == other_title
                        evaluate(
                            source.window,
                            "void window.pywebview.api.set_window_title("
                            + json.dumps(original_title)
                            + ")",
                        )
                        wait_for(
                            lambda source=source, title=original_title: (
                                source.window.title == title
                            ),
                            "Bridge title restoration failed",
                        )
                    checks.append("both injected bridges target their own window")
                    assert evaluate(second.window, "location.origin") == record.base_url
                    assert (
                        evaluate(
                            second.window,
                            "new URL(location.href).searchParams.get('run')",
                        )
                        == run_id
                    )
                    assert run_id in str(evaluate(second.window, "document.title"))
                    checks.append(
                        "real bridge creates exact-run WebView on the same service"
                    )
                    assert (
                        evaluate(
                            first.window,
                            "(() => { const e = document.querySelector("
                            + json.dumps(f'[title="Inspect run {retained}"]')
                            + "); if (!e) return false; e.click(); return true; })()",
                        )
                        is True
                    )
                    wait_for(
                        lambda: (
                            retained in text_content(first.window, "run-detail-header")
                        ),
                        "Main selection did not change",
                    )
                    assert run_id in text_content(second.window, "run-detail-header")
                    checks.append(
                        "main selection changes without changing the secondary run"
                    )
                    wait_for(
                        lambda: "1 records" in text_content(second.window, "data-card"),
                        "Live first record did not render",
                    )
                    assert (
                        evaluate(
                            second.window,
                            "(() => { const span = "
                            "[...document.querySelectorAll('summary span')]"
                            ".find(e => e.textContent.trim() === 'Raw records');"
                            "if (!span) return false; "
                            "const summary = span.closest('summary');"
                            "summary.click(); return summary.parentElement.open; })()",
                        )
                        is True
                    )
                    wait_for(
                        lambda: (
                            '"point_index": 0'
                            in text_content(second.window, "measurement-preview")
                        ),
                        "Real measurement read did not render",
                    )
                    for _ in range(3):
                        assert detail().snapshot == initial.snapshot
                        assert runs() == baseline
                    assert (reports / "ingests.json").read_text() == "1"
                    assert runtime.status().record == record
                    checks.append(
                        "reads retain run set, snapshot and held acquisition count"
                    )
                    check_storage("after_reads")
                    second.window.destroy()
                    assert second.window.events.closed.wait(10), (
                        "Secondary native window did not close"
                    )
                    assert len(webview.windows) == 1 and not closing.is_set()
                    assert retained in text_content(first.window, "run-detail-header")
                    assert detail().control.state == "leased"
                    assert detail().control.cancellation_requested_at is None
                    assert runtime.status().record == record
                    assert child.poll() is None
                    check_storage("after_secondary_close")
                    checks.append(
                        "secondary close leaves service and acquisition running"
                    )
                    (reports / "release").touch()
                    assert child.wait(timeout=45) == 0
                    final = detail()
                    assert final.snapshot.outcome is not None
                    assert final.snapshot.outcome.result == "succeeded"
                    assert (reports / "completed").read_text() == run_id
                    assert runs() == baseline
                    checks.append(
                        "original run completes after secondary close with no extra run"
                    )
                    evidence["status"] = "passed"
            except Exception:
                evidence["error"] = traceback.format_exc()
            finally:
                closing.set()
                windows.destroy()

        # Match desktop.main defaults; never patch the backend/store to pass.
        webview.start(exercise)
        assert evidence["status"] == "passed", evidence.get(
            "error", "GUI exited before checks completed"
        )
    except Exception:
        evidence["status"] = "failed"
        evidence.setdefault("error", traceback.format_exc())
        raise
    finally:
        try:
            if child is not None and child.poll() is None:
                terminate_validation_process_tree(child, owner=child_owner)
            runtime.stop()
        except Exception:
            evidence["status"] = "failed"
            evidence["cleanup_error"] = traceback.format_exc()
            raise
        finally:
            (reports / "result.json").write_text(
                json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
            )


def verify(app: Path, home: Path) -> None:
    require_hosted_runner(home)
    app, home = app.resolve(), home.resolve()
    if home.is_relative_to(app):
        raise ValueError("Acceptance home must be outside the application")
    reports = home / "native-windows"
    reports.mkdir(exist_ok=False)
    script = Path(__file__).resolve()
    selected = ApplicationRuntime(home / "data").installation()
    assert selected.delivery_root is not None
    manifest = selected.delivery_root / "bundle.json"
    (reports / "identity.json").write_text(
        json.dumps(
            {
                "bundle_sha256": file_hash(manifest),
                "bundle": json.loads(manifest.read_bytes()),
                "probe_sha256": file_hash(script),
                "commit": os.environ.get("GITHUB_SHA"),
                "runner": os.environ.get("RUNNER_NAME"),
                "image": os.environ.get("ImageVersion"),  # noqa: SIM112
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    with tempfile.TemporaryDirectory(prefix="host-", dir=reports) as directory:
        copied = Path(directory) / app.name
        shutil.copytree(app, copied, symlinks=True)
        resources = copied / (
            "Contents/Resources" if sys.platform == "darwin" else "resources"
        )
        executable = copied / (
            "Contents/MacOS/Scopecat" if sys.platform == "darwin" else "Scopecat.exe"
        )
        (resources / "bootstrap.py").write_text(
            "import runpy, sys\n"
            f"sys.argv = [{str(script)!r}, '--probe', {str(home)!r}]\n"
            f"runpy.run_path({str(script)!r}, run_name='__main__')\n",
            encoding="utf-8",
        )
        failure: str | None = None
        try:
            # Keep the native executable/bundle identity and finalization path.
            # --check-result only suppresses the launcher's modal failure alert.
            run_bounded(
                [str(executable), "--check-result"], reports / "host.log", timeout=210
            )
            result = cast(
                "dict[str, object]", json.loads((reports / "result.json").read_bytes())
            )
            assert result["status"] == "passed"
        except Exception:
            failure = traceback.format_exc()
            raise
        finally:
            # The service is deliberately detached from the GUI; recover using
            # its isolated home's PID + creation-time ownership checks even if
            # a GUI thread hangs and the outer watchdog kills the native host.
            try:
                run_bounded(
                    [
                        str(selected.python),
                        "-I",
                        "-B",
                        str(script),
                        "--cleanup",
                        str(home),
                    ],
                    reports / "cleanup.log",
                    timeout=45,
                )
            except Exception:
                failure = (failure or "") + traceback.format_exc()
                raise
            finally:
                output = reports / "result.json"
                result = (
                    cast("dict[str, object]", json.loads(output.read_bytes()))
                    if output.exists()
                    else {}
                )
                result["status"] = "failed" if failure else "passed"
                result["host_exit_and_cleanup"] = "failed" if failure else "passed"
                if failure:
                    result["host_error"] = failure
                output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1] == "--check-hosted":
        require_hosted_runner(Path(sys.argv[2]))
    elif sys.argv[1] == "--probe":
        probe(Path(sys.argv[2]))
    elif sys.argv[1] == "--cleanup":
        cleanup(Path(sys.argv[2]))
    else:
        verify(Path(sys.argv[1]), Path(sys.argv[2]))
