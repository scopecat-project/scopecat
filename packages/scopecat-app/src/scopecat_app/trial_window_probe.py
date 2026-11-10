"""Explicit one-shot trial WebView assertions, never human/visual qualification.

Enter through the marked native launcher --trial-window-probe, with its exact
locator environment and --home. The trial must already be prepared and stopped.
No package rewriting,
CUA binding, restart loop, hosted-harness bypass or real-device fixture is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import traceback
from collections.abc import Callable
from importlib.metadata import distribution
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import psutil

    from scopecat.trial_policy import TrialPolicy
    from scopecat_app.desktop import DesktopView, DesktopWindows


def guard(home: Path) -> TrialPolicy:
    # Keep all application imports and all state access after this boundary.
    from scopecat.trial_policy import checked_path, current_policy, validate_environment

    if sys.flags.optimize:
        raise ValueError("Probe assertions must not be disabled")
    policy = current_policy()
    if policy is None:
        raise ValueError("Only a marked trial interpreter may run this probe")
    validate_environment(policy)
    if checked_path(home) != policy.home:
        raise ValueError("Probe home must exactly match the marked trial")
    return policy


def wait(check: Callable[[], object], message: str, timeout: float = 15) -> object:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError(message)


def is_reopened_controller(
    candidate: DesktopView, main: DesktopView, previous: DesktopView
) -> bool:
    """Closing the old controller makes main latest before the reopen arrives."""
    return (
        candidate is not main
        and candidate is not previous
        and previous.device_id is not None
        and candidate.device_id == previous.device_id
    )


def exercise(windows: DesktopWindows, main: DesktopView, base_url: str) -> list[str]:
    """Interact only with the windows created by this process."""
    import httpx2

    passed: list[str] = []

    def ready(view: DesktopView) -> None:
        wait(view.loaded.is_set, "Window did not load", 30)

    def js(view: DesktopView, script: str) -> object:
        return cast("object", view.window.evaluate_js(script))

    def text(view: DesktopView, value: str) -> bool:
        return bool(js(view, f"document.body.innerText.includes({json.dumps(value)})"))

    def button(view: DesktopView, label: str) -> None:
        query = (
            "[...document.querySelectorAll('button')].find(e=>"
            f"e.textContent.trim()==={json.dumps(label)} && !e.disabled)"
        )
        wait(lambda: js(view, f"Boolean({query})"), f"Missing enabled {label}")
        js(view, f"{query}.click()")

    def fill(view: DesktopView, label: str, value: str) -> None:
        script = """(()=>{const label=[...document.querySelectorAll('label')]
        .find(e=>e.textContent.trim()===LABEL);
        const e=label&&(label.control||label.querySelector('input,select,textarea'));
        if(!e)return false;
        const proto=e.tagName==='SELECT'?HTMLSelectElement.prototype:
          e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:
          HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto,'value').set.call(e,VALUE);
        e.dispatchEvent(new Event('input',{bubbles:true}));
        e.dispatchEvent(new Event('change',{bubbles:true}));return true;})()"""
        wait(
            lambda: js(
                view,
                script.replace("LABEL", json.dumps(label)).replace(
                    "VALUE", json.dumps(value)
                ),
            ),
            f"Missing input {label}",
        )

    with httpx2.Client(base_url=base_url, trust_env=False, timeout=5) as client:
        for path in ("/api/v1/devices", "/api/v1/direct-measurements"):
            response = client.get(path)
            response.raise_for_status()
            document = cast("dict[str, object]", response.json())
            assert document.get("items") == [], "Probe requires empty trial fixtures"
    ready(main)
    js(main, "location.hash='settings'")
    fill(main, "操作者名称", "Isolated native program probe")
    button(main, "保存操作者")
    wait(lambda: text(main, "操作者已保存"), "Operator save did not complete")
    js(main, "location.hash='instruments'")
    button(main, "启用模拟设备体验（无硬件）")
    button(main, "添加设备")
    fill(main, "设备名称", "Trial probe virtual VNA")
    fill(main, "驱动", "scopecat.virtual.vna")
    button(main, "保存设备")
    button(main, "操作")
    wait(lambda: windows.latest is not main, "Dedicated controller not created")
    control = windows.latest
    ready(control)
    button(control, "连接")
    wait(lambda: text(control, "交互会话已连接"), "Connection missing")
    # A real native closing event, not synthetic preflight-success replies.
    control.window.destroy()
    wait(control.close_finished.is_set, "Empty controller did not close")
    passed.append("empty-controller-single-close")
    previous = control
    button(main, "操作")
    wait(
        lambda: is_reopened_controller(windows.latest, main, previous),
        "Controller not reopened with the same device identity",
    )
    control = windows.latest
    ready(control)
    button(control, "连接")
    wait(lambda: text(control, "交互会话已连接"), "Reconnect missing")
    # Match the observed production title without depending on translated text.
    sweep = (
        "[...document.querySelectorAll('button')]"
        ".find(e=>e.title==='scopecat.network_sweep/v1 / ')"
    )
    for index in range(2):
        wait(
            lambda: js(control, f"Boolean({sweep}&&!{sweep}.disabled)"),
            "Sweep unavailable",
        )
        js(control, f"{sweep}.click()")
        wait(
            lambda index=index: (
                js(
                    control,
                    "document.querySelectorAll('[data-testid^=recent-result-]').length",
                )
                == index + 1
            ),
            "Receipt missing",
        )
        if index == 0:
            control.window.destroy()
            control.window.destroy()
            wait(lambda: text(control, "结束控制前的待办"), "Close checklist absent")
            assert js(control, "document.querySelectorAll('[role=dialog]').length") == 1
            assert js(
                control,
                "[...document.querySelectorAll('[data-testid^=recent-result-]')].every(e=>e.textContent.includes('待保存'))",
            )
            button(control, "继续控制")
            wait(
                lambda: not text(control, "结束控制前的待办"), "Cancel did not dismiss"
            )
            assert not text(control, "Input is not confirmed saved")
            assert (
                js(
                    control,
                    "document.querySelectorAll('[data-testid^=recent-result-]').length",
                )
                == 1
            )
            passed.append("repeated-close-one-dialog-cancel-keeps-pending-no-error")
        button(control, "保存所选结果")
        fill(control, "标题", f"Native program result {index + 1}")
        button(control, "保存")
        wait(
            lambda: (
                js(control, "document.querySelectorAll('[role=dialog]').length") == 0
            ),
            "Save dialog did not complete",
        )
        wait(lambda: text(control, "查看已保存结果"), "Saved receipt absent")
    with httpx2.Client(base_url=base_url, trust_env=False, timeout=5) as client:
        response = client.get("/api/v1/direct-measurements")
        response.raise_for_status()
        document = cast("dict[str, list[dict[str, object]]]", response.json())
        records = document["items"]
    assert len(records) == 2, "Probe requires fresh synthetic data"
    ids = [str(record["id"]) for record in records]
    control.api.open_saved_measurement_window(ids[0])
    a = windows.latest
    ready(a)
    wait(lambda: text(a, "Native program result"), "Result A did not render")
    control.api.open_saved_measurement_window(ids[0])
    assert windows.latest is a
    control.api.open_saved_measurement_window(ids[1])
    b = windows.latest
    ready(b)
    assert (
        b is not a
        and not control.close_finished.is_set()
        and not a.close_finished.is_set()
    )
    passed.append("same-id-reuse-different-id-coexist-controller-retained")
    control.window.destroy()
    wait(lambda: text(control, "结束控制前的待办"), "Release checklist absent")
    button(control, "保持当前状态并结束")
    wait(control.close_finished.is_set, "Original close intent did not continue")
    assert not a.close_finished.is_set() and not b.close_finished.is_set()
    passed.append("keep-current-completes-original-close-results-retained")
    return passed


COCOA_VERSION = "6.2.1+scopecat.2"
COCOA_SHA256 = "af84442e09b10f943b700e3081ff7682869c85ac27e6784c9b1e76fed13a50ee"


def check_cocoa_identity(
    identity: str | None, expected: str, version: str, digest: str
) -> None:
    if identity != expected:
        raise ValueError("Trial probe requires its original unique Cocoa host")
    if version != COCOA_VERSION or digest != COCOA_SHA256:
        raise ValueError("Trial probe requires the reviewed private-store Cocoa patch")


def platform_preconditions(policy: TrialPolicy) -> None:
    if sys.platform != "darwin":
        return
    from scopecat.trial_policy import checked_path
    from scopecat_app.desktop_platform import macos_bundle_identifier

    package = distribution("pywebview")
    source = checked_path(Path(str(package.locate_file("webview/platforms/cocoa.py"))))
    if not source.is_relative_to(policy.resources / "python"):
        raise ValueError("Cocoa backend must belong to this trial package")
    spec = find_spec("webview")
    expected_module = checked_path(
        Path(str(package.locate_file("webview/__init__.py")))
    )
    if (
        spec is None
        or spec.origin is None
        or checked_path(Path(spec.origin)) != expected_module
    ):
        raise ValueError("Loaded WebView must match the verified trial dependency")
    check_cocoa_identity(
        macos_bundle_identifier(),
        "org.scopecat.trial." + policy.identity,
        package.version,
        hashlib.sha256(source.read_bytes()).hexdigest(),
    )


def run(home: Path) -> None:
    policy = guard(home)
    platform_preconditions(policy)
    import psutil
    import webview

    from scopecat_app.application_runtime import ApplicationRuntime
    from scopecat_app.desktop import DesktopWindows
    from scopecat_app.desktop_session import DesktopSession

    report = policy.home / "trial-window-probe.json"
    # Do not attach to a daemon or overwrite a prior probe's records.
    if os.path.lexists(report):
        raise ValueError("Use a fresh trial for each probe")
    runtime = ApplicationRuntime(policy.state)
    if runtime.status().state != "stopped":
        raise ValueError("Trial must be prepared and stopped; never attach or restart")
    evidence: dict[str, object] = {
        "status": "failed",
        "scope": "native-window-program-assertions-not-visual",
        "trial": policy.identity,
    }
    guard(home)
    report_file = report.open("x", encoding="utf-8")
    report_lock = threading.Lock()

    def write_report() -> None:
        guard(home)
        with report_lock:
            report_file.seek(0)
            report_file.write(json.dumps(evidence, indent=2))
            report_file.truncate()
            report_file.flush()

    owned: list[psutil.Process] = []
    finished = threading.Event()
    ownership_lock = threading.Lock()
    own_process = psutil.Process()

    def observe_children() -> None:
        # A handle is retained with its creation identity before reparenting.
        while not finished.wait(0.05):
            for child in own_process.children(recursive=True):
                with ownership_lock:
                    if child not in owned:
                        owned.append(child)

    threading.Thread(target=observe_children, daemon=True).start()

    def terminate_owned() -> None:
        with ownership_lock:
            processes = tuple(owned)
        for process in reversed(processes):
            try:
                if process.is_running():
                    process.terminate()
                    try:
                        process.wait(3)
                    except psutil.TimeoutExpired:
                        process.kill()
            except psutil.NoSuchProcess:
                pass

    def deadline() -> None:
        if not finished.wait(150):
            evidence["error"] = "Hard 150-second probe deadline exceeded"
            evidence["startup_cleanup"] = (
                "Only observed owned descendants are stopped; a startup without "
                "a returned daemon record may require separate ownership inspection"
            )
            try:
                write_report()
            finally:
                terminate_owned()
                os._exit(2)  # This probe process only; no application relaunch.

    threading.Thread(target=deadline, daemon=True).start()
    session = DesktopSession(runtime, threading.Event())
    windows = DesktopWindows(session, lambda: None)
    try:
        record = runtime.start()  # Exactly one startup, never retry/restart.
        process = psutil.Process(record.pid)
        assert process.create_time() == record.process_create_time, (
            "Daemon PID identity changed; no signal sent to this PID"
        )
        with ownership_lock:
            owned.append(process)
        assert record.data_root == runtime.instance.runtime_binding.data_root
        session.connected(record.base_url)
        main = windows.create()

        def callback() -> None:
            try:
                evidence["passed"] = exercise(windows, main, record.base_url)
                evidence["status"] = "passed"
            except Exception:
                evidence["error"] = traceback.format_exc()
            finally:
                # Dispose only windows owned by this session after assertions.
                session.closing.set()
                for view in tuple(windows._views):  # pyright: ignore[reportPrivateUsage] - owned test session
                    view.close_status = "saved"
                    view.window.destroy()

        webview.start(
            callback,
            private_mode=True,
            gui="cocoa" if sys.platform == "darwin" else None,
        )
    except Exception:
        evidence["error"] = traceback.format_exc()
        raise
    finally:
        finished.set()
        terminate_owned()
        try:
            write_report()
        finally:
            report_file.close()
    if evidence["status"] != "passed":
        raise AssertionError(str(evidence.get("error", "Probe did not finish")))


def parse_home(arguments: list[str]) -> Path:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--home", type=Path, required=True, action="append")
    homes = cast("list[Path]", parser.parse_args(arguments).home)
    if len(homes) != 1:
        parser.error("Exactly one --home is required")
    return homes[0]


if __name__ == "__main__":
    run(parse_home(sys.argv[1:]))
