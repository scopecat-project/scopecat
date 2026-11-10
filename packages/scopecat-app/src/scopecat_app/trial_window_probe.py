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
from importlib import import_module
from importlib.metadata import distribution
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

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


NAVIGATION_ACTION = r"""(()=>{
  const targets=[...document.querySelectorAll('nav button')]
    .filter(e=>e.getAttribute('aria-label')===LABEL);
  const ready=Boolean(document.querySelector('nav button[aria-current=page]'))
    &&targets.length===1&&!targets[0].disabled;
  if(ACTION==='ready')return ready;
  if(!ready)throw new Error('Probe navigation target is not uniquely ready');
  if(ACTION==='click'){targets[0].click();return true;}
  if(ACTION==='selected')return targets[0].getAttribute('aria-current')==='page';
  throw new Error('Unknown probe navigation action');
})()"""


def navigation_script(label: str, action: str) -> str:
    return NAVIGATION_ACTION.replace("LABEL", json.dumps(label)).replace(
        "ACTION", json.dumps(action)
    )


def navigate_workspace(evaluate: Callable[[str], object], label: str) -> None:
    """Wait for React navigation, then use its unique actual button."""
    wait(
        lambda: evaluate(navigation_script(label, "ready")),
        f"React workspace navigation not ready: {label}",
    )
    evaluate(navigation_script(label, "click"))
    wait(
        lambda: evaluate(navigation_script(label, "selected")),
        f"Workspace navigation did not select: {label}",
    )


PAGE_DIAGNOSTICS = r"""(()=>{
  if(location.origin!==EXPECTED_ORIGIN)
    return {error:'Diagnostic page is not the owned daemon origin'};
  const visible=node=>{
    for(let e=node;e;e=e.parentElement){
      const css=getComputedStyle(e);
      if(e.hidden||e.getAttribute('aria-hidden')==='true'
        ||css.display==='none'||css.visibility==='hidden'
        ||css.visibility==='collapse')return false;
    }
    return true;
  };
  const sanitized=node=>{
    if(!visible(node))return '';
    const copy=node.cloneNode(true);
    const originals=[...node.querySelectorAll('*')];
    const clones=[...copy.querySelectorAll('*')];
    originals.forEach((e,i)=>{
      const css=getComputedStyle(e);
      if(e.matches('input,select,textarea,[contenteditable],script,style,template')
        ||e.hidden||e.getAttribute('aria-hidden')==='true'
        ||css.display==='none'||css.visibility==='hidden')clones[i].remove();
    });
    return copy.textContent;
  };
  return {
    url:location.href,readyState:document.readyState,
    visibleText:sanitized(document.body).slice(0,12000),
    labels:[...document.querySelectorAll('label')].filter(visible).slice(0,60).map(e=>({
      text:sanitized(e).slice(0,160),htmlFor:e.htmlFor,
      control:e.control?{tag:e.control.tagName,id:e.control.id}:null,
      children:[...e.children].map(c=>({tag:c.tagName,role:c.getAttribute('role')}))
    })),
    controls:[...document.querySelectorAll('input,select,textarea,button')]
      .slice(0,100).map(e=>({tag:e.tagName,id:e.id,type:e.type,
        ariaLabel:e.getAttribute('aria-label'),
        labelledBy:e.getAttribute('aria-labelledby'),disabled:e.disabled}))
  };
})()"""


def page_diagnostics(view: DesktopView, base_url: str) -> object:
    """Only inspect our same-origin synthetic page; exclude values and storage."""
    location = view.window.get_current_url()
    from urllib.parse import urlsplit

    expected, actual = urlsplit(base_url), urlsplit(location or "")
    if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
        return {"error": "Diagnostic page is not the owned daemon origin"}
    origin = f"{expected.scheme}://{expected.netloc}"
    return cast(
        "object",
        view.window.evaluate_js(
            PAGE_DIAGNOSTICS.replace("EXPECTED_ORIGIN", json.dumps(origin))
        ),
    )


def collect_diagnostics(windows: DesktopWindows, base_url: str) -> list[object]:
    diagnostics: list[object] = []
    for view in tuple(windows._views):  # pyright: ignore[reportPrivateUsage] - owned session
        if not view.close_finished.is_set():
            try:
                diagnostics.append(page_diagnostics(view, base_url))
            except Exception as error:
                diagnostics.append({"diagnostic_error": str(error)})
    return diagnostics


FIELD_ASSIGNMENT = r"""(()=>{
  const labels=[...document.querySelectorAll('label')].filter(label=>{
    const copy=label.cloneNode(true);
    copy.querySelectorAll('input,select,textarea').forEach(e=>e.remove());
    return copy.textContent.trim()===LABEL;
  });
  if(labels.length===0)return false;
  if(labels.length!==1)throw new Error('Ambiguous probe label');
  const label=labels[0];
  const controls=[...new Set([...(label.control?[label.control]:[]),
    ...label.querySelectorAll('input,select,textarea')])];
  if(controls.length!==1)throw new Error('Ambiguous probe control');
  const e=controls[0];
  if(e.disabled)return false;
  const proto=e.tagName==='SELECT'?HTMLSelectElement.prototype:
    e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:
    e.tagName==='INPUT'?HTMLInputElement.prototype:null;
  if(!proto)throw new Error('Unsupported probe control');
  if(e.tagName==='SELECT'&&![...e.options].some(o=>o.value===VALUE))
    throw new Error('Missing exact probe select option');
  Object.getOwnPropertyDescriptor(proto,'value').set.call(e,VALUE);
  e.dispatchEvent(new Event('input',{bubbles:true}));
  e.dispatchEvent(new Event('change',{bubbles:true}));
  return true;
})()"""


def field_assignment(label: str, value: str) -> str:
    return FIELD_ASSIGNMENT.replace("LABEL", json.dumps(label)).replace(
        "VALUE", json.dumps(value)
    )


def close_preflight_idle(windows: DesktopWindows, view: DesktopView) -> bool:
    return (
        view.close_request is None
        and not view.close_in_progress
        and not windows._preflight_lock.locked()  # pyright: ignore[reportPrivateUsage] - own test session
    )


class _NativeWindow(Protocol):
    def performClose_(self, sender: object) -> None: ...


class _NativeView(Protocol):
    pywebview_window: object
    window: _NativeWindow


class _NativeRegistry(Protocol):
    instances: dict[str, _NativeView]


class _MainThread(Protocol):
    def callAfter(self, action: Callable[[object], None], argument: object) -> None: ...


class _NativeBackend(Protocol):
    BrowserView: _NativeRegistry
    AppHelper: _MainThread


def schedule_native_close(view: DesktopView) -> None:
    """Request the owned window's user-close path, not unconditional destruction."""
    if sys.platform == "darwin":
        cocoa = cast(
            "_NativeBackend", cast("object", import_module("webview.platforms.cocoa"))
        )
        instances = cocoa.BrowserView.instances
        native = instances.get(view.window.uid)
        if native is None or native.pywebview_window is not view.window:
            raise ValueError("Native close target is not this probe's window")
        window = native.window
        if cast("object", view.window.native) is not window:
            raise ValueError("Cocoa native window identity mismatch")
        # NSWindow.close bypasses windowShouldClose_. performClose_ invokes it.
        cocoa.AppHelper.callAfter(window.performClose_, None)
    elif sys.platform == "win32":
        winforms = cast(
            "_NativeBackend",
            cast("object", import_module("webview.platforms.winforms")),
        )
        instances = winforms.BrowserView.instances
        native = instances.get(view.window.uid)
        if native is None or native.pywebview_window is not view.window:
            raise ValueError("Native close target is not this probe's window")
        if cast("object", view.window.native) is not native:
            raise ValueError("WinForms native window identity mismatch")
        # Reviewed WinForms destroy_window invokes Form.Close -> FormClosing.
        view.window.destroy()
    else:
        raise ValueError("Native close probe supports only Cocoa and WinForms")


def request_native_close(view: DesktopView) -> None:
    """Require an actual closing callback for every requested close."""
    observed = threading.Event()

    def acknowledged() -> None:
        observed.set()

    view.window.events.closing += acknowledged
    try:
        schedule_native_close(view)
        wait(observed.is_set, "Native user-close event was not observed")
    finally:
        view.window.events.closing -= acknowledged


CONTROL_WINDOW_LABEL = "打开控制窗口"


def exercise(
    windows: DesktopWindows, main: DesktopView, base_url: str, passed: list[str]
) -> list[str]:
    """Interact only with the windows created by this process."""
    import httpx2

    def ready(view: DesktopView) -> None:
        wait(view.loaded.is_set, "Window did not load", 30)

    def js(view: DesktopView, script: str) -> object:
        return cast("object", view.window.evaluate_js(script))

    def text(view: DesktopView, value: str) -> bool:
        return bool(js(view, f"document.body.innerText.includes({json.dumps(value)})"))

    def button(view: DesktopView, label: str) -> None:
        query = (
            "[...document.querySelectorAll('button')].filter(e=>"
            f"e.textContent.trim()==={json.dumps(label)} && !e.disabled)"
        )
        wait(lambda: js(view, f"{query}.length===1"), f"Missing unique enabled {label}")
        js(view, f"{query}[0].click()")

    def fill(view: DesktopView, label: str, value: str) -> None:
        wait(
            lambda: js(
                view,
                field_assignment(label, value),
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
    navigate_workspace(lambda script: js(main, script), "设置 / Settings")
    fill(main, "操作者名称", "Isolated native program probe")
    button(main, "保存操作者")
    wait(lambda: text(main, "操作者已保存"), "Operator save did not complete")
    navigate_workspace(
        lambda script: js(main, script), "设备与驱动 / Devices and drivers"
    )
    button(main, "启用模拟设备体验（无硬件）")
    button(main, "添加设备")
    fill(main, "设备名称", "Trial probe virtual VNA")
    fill(main, "驱动", "scopecat.virtual.vna")
    button(main, "保存设备")
    button(main, CONTROL_WINDOW_LABEL)
    wait(lambda: windows.latest is not main, "Dedicated controller not created")
    control = windows.latest
    ready(control)
    button(control, "连接")
    wait(lambda: text(control, "交互会话已连接"), "Connection missing")
    # A real native closing event, not synthetic preflight-success replies.
    request_native_close(control)
    wait(control.close_finished.is_set, "Empty controller did not close")
    passed.append("empty-controller-native-closing-event-observed")
    with httpx2.Client(base_url=base_url, trust_env=False, timeout=5) as client:

        def released() -> bool:
            response = client.get("/api/v1/devices")
            response.raise_for_status()
            rows = cast("dict[str, list[dict[str, object]]]", response.json())["items"]
            matches = [
                row
                for row in rows
                if cast("dict[str, object]", row["device"])["id"] == control.device_id
            ]
            return len(matches) == 1 and matches[0]["availability"] == "idle"

        wait(released, "Closed controller still owns the device on the service")
    passed.append("empty-controller-service-ownership-released")
    passed.append("empty-controller-single-close")
    previous = control
    button(main, CONTROL_WINDOW_LABEL)
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
            request_native_close(control)
            request_native_close(control)
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
            wait(
                lambda: close_preflight_idle(windows, control),
                "Cancelled native close has not finished",
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
        titles: dict[str, str] = {}
        for record in records:
            result = client.get(f"/api/v1/direct-measurements/{record['id']}")
            result.raise_for_status()
            detail = cast("dict[str, dict[str, str]]", result.json())
            titles[str(record["id"])] = detail["selection"]["title"]
    ids = [str(record["id"]) for record in records]
    assert set(titles.values()) == {
        "Native program result 1",
        "Native program result 2",
    }

    def result_ready(view: DesktopView, record_id: str) -> None:
        ready(view)
        assert view.saved_measurement_id == record_id
        wait(
            lambda: js(
                view,
                "document.querySelector('h1')?.textContent==="
                + json.dumps(titles[record_id]),
            ),
            f"Saved result {record_id} did not render its exact title",
        )

    control.api.open_saved_measurement_window(ids[0])
    a = windows.latest
    result_ready(a, ids[0])
    control.api.open_saved_measurement_window(ids[0])
    assert windows.latest is a
    control.api.open_saved_measurement_window(ids[1])
    b = windows.latest
    result_ready(b, ids[1])
    assert (
        b is not a
        and not control.close_finished.is_set()
        and not a.close_finished.is_set()
    )
    passed.append("same-id-reuse-different-id-coexist-controller-retained")
    request_native_close(control)
    wait(lambda: text(control, "结束控制前的待办"), "Release checklist absent")
    button(control, "保持当前状态并结束")
    wait(control.close_finished.is_set, "Original close intent did not continue")
    assert not a.close_finished.is_set() and not b.close_finished.is_set()
    passed.append("keep-current-completes-original-close-results-retained")
    return passed


WINFORMS_VERSION = "6.2.1+scopecat.windows.2"
WINFORMS_SHA256 = "dc42082a8d6ae25189a958fcfec8310131c4f2c834a8cd81508fd913054b2d49"


COCOA_VERSION = "6.2.1+scopecat.2"
COCOA_SHA256 = "af84442e09b10f943b700e3081ff7682869c85ac27e6784c9b1e76fed13a50ee"


def check_cocoa_identity(
    identity: str | None, expected: str, version: str, digest: str
) -> None:
    if identity != expected:
        raise ValueError("Trial probe requires its original unique Cocoa host")
    if version != COCOA_VERSION or digest != COCOA_SHA256:
        raise ValueError("Trial probe requires the reviewed private-store Cocoa patch")


def check_winforms_identity(version: str, digest: str) -> None:
    if version != WINFORMS_VERSION or digest != WINFORMS_SHA256:
        raise ValueError("Trial probe requires the reviewed WinForms backend")


def platform_preconditions(policy: TrialPolicy) -> None:
    if sys.platform not in {"darwin", "win32"}:
        raise ValueError("Trial native probe requires Cocoa or WinForms")
    from scopecat.trial_policy import checked_path
    from scopecat_app.desktop_platform import macos_bundle_identifier

    package = distribution("pywebview")
    backend_name = "cocoa" if sys.platform == "darwin" else "winforms"
    source = checked_path(
        Path(str(package.locate_file(f"webview/platforms/{backend_name}.py")))
    )
    if not source.is_relative_to(policy.resources / "python"):
        raise ValueError("Native backend must belong to this trial package")
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
    if sys.platform == "win32":
        check_winforms_identity(
            package.version, hashlib.sha256(source.read_bytes()).hexdigest()
        )
        return
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
                passed: list[str] = []
                evidence["passed"] = passed
                exercise(windows, main, record.base_url, passed)
                evidence["status"] = "passed"
            except Exception:
                evidence["error"] = traceback.format_exc()
                evidence["owned_page_diagnostics"] = collect_diagnostics(
                    windows, record.base_url
                )
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
