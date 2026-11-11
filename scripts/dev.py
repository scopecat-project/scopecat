"""One source desktop, Vite and real backend per retained development home."""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import httpx2
import psutil
from filelock import FileLock, Timeout

from scopecat.runtime_binding import load_runtime_binding
from scopecat_app.application_runtime import ApplicationRuntime, write_state
from scopecat_app.desktop_session import DesktopSession
from scopecat_server.development_ui import RECORD_NAME, DevelopmentUI
from scopecat_server.validation_process import terminate_validation_process_tree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.app_build.dev_resources import (
    development_home,
    prepare_native_dependency,
    prepare_resources_logged,
    source_stamp,
    trial_development_policy,
)

if TYPE_CHECKING:
    from scopecat.trial_policy import TrialPolicy


def trial_vite_script(
    policy: TrialPolicy, ui: Path, entry: Path, endpoint: Path, port: int
) -> str:
    """The Node child checks inherited scope before loading any source or dependency."""
    expected = {
        "SCOPECAT_TRIAL_DEVELOPMENT": policy.identity,
        "SCOPECAT_LOCATOR_HOME": str(policy.locator),
        "SCOPECAT_DEV_ENDPOINT_FILE": str(endpoint),
    }
    config = {
        "root": str(ui),
        "configFile": str(ui / "vite.config.ts"),
        "configLoader": "native",
        "cacheDir": str(policy.state / "vite-cache"),
        "clearScreen": False,
        "server": {"host": "127.0.0.1", "port": port, "strictPort": True},
    }
    return (
        f"for (const [key,value] of Object.entries({json.dumps(expected)})) {{"
        "if (process.env[key] !== value) "
        "throw new Error('Trial Vite inherited scope mismatch: '+key);"
        "}\n"
        f"const {{createServer}} = await import({json.dumps(entry.as_uri())});\n"
        f"const server = await createServer({json.dumps(config)});\n"
        "await server.listen();\n"
    )


class Arguments(Protocol):
    home: Path | None
    temporary: bool
    browser: bool
    trial_development: bool


def source_root() -> Path:
    root = Path(__file__).resolve().parents[1]
    if not (root / "apps/scopecat-ui/vite.config.ts").is_file():
        raise ValueError("Run this entry from an editable public Scopecat checkout.")
    return root


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return cast("tuple[str, int]", sock.getsockname())[1]


def prepare_application(runtime: ApplicationRuntime, source: Path, cache: Path) -> Path:
    if (
        policy := trial_development_policy(source=source, home=runtime.home)
    ) is not None:
        from scopecat.trial_policy import checked_path
        from scopecat_app.trial_window_probe import platform_preconditions

        platform_preconditions(policy)
        payload = checked_path(policy.resources / "payload")
        selected = (
            runtime.installation()
        )  # Must already be prepared by the native package.
        if (
            checked_path(selected.python) != checked_path(Path(sys.executable))
            or selected.delivery_root is None
            or checked_path(selected.delivery_root) != payload
            or checked_path(selected.static_dir) != payload / "gui"
        ):
            raise ValueError(
                "Trial development requires its prepared package installation"
            )
        return payload
    payload = prepare_resources_logged(
        source, cache, runtime.home / "logs/resources.log"
    )
    if not runtime.selection.exists():
        runtime.configure(static_dir=payload / "gui", delivery_root=payload)
    else:
        candidate = runtime.qualify(
            Path(sys.executable), payload / "gui", delivery_root=payload
        )
        if candidate != runtime.installation() or runtime.pending.exists():
            runtime.select(candidate)
    return payload


def start_vite(
    source: Path, endpoint: Path, log: Path
) -> tuple[subprocess.Popen[str], str]:
    policy = trial_development_policy(source=source)
    ui = source / "apps/scopecat-ui"
    port = available_port()
    if policy is not None:
        from scopecat.trial_policy import checked_path

        if (
            checked_path(endpoint) != policy.state / "backend.json"
            or checked_path(log) != policy.state / "logs/vite.log"
        ):
            raise ValueError(
                "Trial Vite endpoint and log must match its isolated state"
            )
        node = shutil.which("node")
        entry = ui / "node_modules/vite/dist/node/index.js"
        if (
            node is None
            or not entry.is_file()
            or not entry.resolve().is_relative_to(ui / "node_modules")
        ):
            raise ValueError(
                "Prepare this trial source's locked frontend dependencies first"
            )
        command = [
            node,
            "--input-type=module",
            "-e",
            trial_vite_script(policy, ui, entry, endpoint, port),
        ]
    else:
        pnpm = shutil.which("pnpm")
        if pnpm is None:
            raise ValueError(
                "Install Node.js and pnpm before starting source development."
            )
        subprocess.run([pnpm, "install", "--frozen-lockfile"], cwd=ui, check=True)  # noqa: S603
        command = [
            pnpm,
            "exec",
            "vite",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--strictPort",
            "--clearScreen",
            "false",
        ]
    url = f"http://127.0.0.1:{port}"
    environment = dict(os.environ, SCOPECAT_DEV_ENDPOINT_FILE=str(endpoint))
    if policy is not None:
        for name in ("NODE_OPTIONS", "NODE_PATH", "NODE_COMPILE_CACHE"):
            environment.pop(name, None)
    with log.open("ab") as output:
        process = subprocess.Popen(  # noqa: S603 - fixed source tool, loopback only
            command,
            cwd=ui,
            env=environment,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
    try:
        with httpx2.Client(timeout=1, trust_env=False) as client:
            for _ in range(150):
                if process.poll() is not None:
                    raise ValueError(f"Vite exited; see {log}")
                try:
                    if client.get(url).status_code == 200:
                        return process, url
                except httpx2.HTTPError:
                    pass
                time.sleep(0.2)
        raise ValueError(f"Vite startup timed out; see {log}")
    except BaseException:
        terminate_validation_process_tree(process, owner=psutil.Process(process.pid))
        raise


def queue_quit(commands: queue.Queue[str], pending: threading.Event) -> None:
    """Coalesce console signals until the current quit request returns."""
    if not pending.is_set():
        pending.set()
        commands.put("q")


def control_session(
    session: DesktopSession,
    source: Path,
    commands: queue.Queue[str],
    prepare: Callable[[], None],
    frontend: subprocess.Popen[str] | None = None,
    frontend_log: Path | None = None,
    quit_requested: threading.Event | None = None,
) -> None:
    """Foreground commands share native lifecycle fencing; no forced fallback."""
    stamp: str | None = None
    restart_pending = False
    frontend_failed = False
    next_check = time.monotonic() + 2
    while not session.closing.wait(0.2):
        if frontend is not None and not frontend_failed and frontend.poll() is not None:
            frontend_failed = True
            message = (
                f"Vite exited ({frontend.returncode}); "
                "native/browser UI is unavailable. "
                f"Backend work is retained. Log: {frontend_log}. "
                "Enter q or press Ctrl-C to quit after idle, then relaunch."
            )
            print(message, flush=True)
            logging.getLogger(__name__).error(message)
        try:
            command = commands.get_nowait()
        except queue.Empty:
            command = ""
        try:
            if command in {"r", "w"}:
                restart_pending = command == "w"
                session.restart(prepare)
                restart_pending = False
                stamp = source_stamp(source)
                print(
                    "Backend restarted safely. Relaunch the command for native "
                    "host Python changes.",
                    flush=True,
                )
            elif command == "q":
                restart_pending = False
                if session.request_exit() is not None:
                    session.wait_for_idle(True)
                    print(
                        "Work is active; waiting for idle to quit. Data and "
                        "processes retained.",
                        flush=True,
                    )
            elif command == "c":
                restart_pending = False
                session.wait_for_idle(False)
                print("Pending restart/quit cancelled.", flush=True)
            if restart_pending:
                session.restart(prepare)
                restart_pending = False
                stamp = source_stamp(source)
                print("Backend restarted after becoming idle.", flush=True)
            session.poll_exit()
        except Exception as error:
            if command or not restart_pending:
                print(f"Operation deferred: {error}", flush=True)
                logging.getLogger(__name__).exception(
                    "Development lifecycle operation deferred"
                )
        finally:
            if command == "q" and quit_requested is not None:
                quit_requested.clear()
        if time.monotonic() >= next_check:
            next_check = time.monotonic() + 2
            try:
                current = source_stamp(source)
                if stamp is not None and current != stamp:
                    print(
                        "Python/package source changed: r + Enter restarts when idle; "
                        "w + Enter waits. GUI changes use HMR.",
                        flush=True,
                    )
                stamp = current
            except Exception as error:
                print(
                    f"Source check deferred; lifecycle remains available: {error}",
                    flush=True,
                )


def stop_when_idle(runtime: ApplicationRuntime) -> None:
    """A failed UI/startup never grants permission to terminate scientific work."""
    if not runtime.selection.exists():
        return
    while True:
        try:
            if runtime.stop_if_idle():
                return
            print("Waiting for backend work before stopping development.", flush=True)
        except Exception as error:
            print(f"Cannot confirm idle; backend retained: {error}", flush=True)
            return
        time.sleep(2)


def claim_home(source: Path, home: Path) -> None:
    policy = trial_development_policy(
        source=source, home=home, entry=Path(__file__).absolute()
    )
    if policy is not None:
        runtime = ApplicationRuntime(home)
        prepare_application(runtime, source, policy.home / "development/resources")
        if runtime.status().state != "stopped":
            raise ValueError("Trial development requires a prepared and stopped trial")
    marker = home / "development.json"
    if marker.exists():
        previous = cast("dict[str, object]", json.loads(marker.read_text()))
        if previous.get("source") != str(source.resolve()):
            raise ValueError(
                "This development home belongs to another checkout; choose "
                "a different --home."
            )
    elif policy is None and any(
        home.joinpath(name).exists()
        for name in (
            "runtime",
            "installation.json",
            "scopecat.application.json",
            "data",
            "state",
        )
    ):
        raise ValueError(
            "This is not an owned development home; choose a new --home. "
            "Existing data retained."
        )
    write_state(
        marker, json.dumps({"source": str(source.resolve()), "home": str(home)})
    )


def publish_endpoint(home: Path, url: str) -> None:
    """Publish proxy routing before any window reload, including native restart."""
    write_state(home / "backend.json", json.dumps({"url": url}))
    marker = home / "development.json"
    report = cast("dict[str, object]", json.loads(marker.read_text()))
    report["backend"] = url
    write_state(marker, json.dumps(report, indent=2))
    root = home / "runtime" if (home / "runtime").exists() else home
    ui_record = load_runtime_binding(root).coordination_root / RECORD_NAME
    if ui_record.is_file():
        current = DevelopmentUI.model_validate_json(ui_record.read_bytes())
        write_state(
            ui_record, current.model_copy(update={"backend": url}).model_dump_json()
        )


def publish_ui(session: DesktopSession, frontend: psutil.Process) -> None:
    assert session.base_url is not None and session.ui_url is not None
    record = DevelopmentUI(
        backend=session.base_url,
        ui=session.ui_url,
        owner_pid=os.getpid(),
        owner_created=psutil.Process().create_time(),
        frontend_pid=frontend.pid,
        frontend_created=frontend.create_time(),
    )
    write_state(
        session.runtime.instance.runtime_binding.coordination_root / RECORD_NAME,
        record.model_dump_json(),
    )


def run(source: Path, home: Path, *, browser: bool) -> None:
    policy = trial_development_policy(
        source=source, home=home, entry=Path(__file__).absolute()
    )
    if policy is not None and browser:
        raise ValueError("Trial development requires its native Windows host")
    home = home.resolve()
    home.mkdir(parents=True, exist_ok=True)
    marker = home / "development.json"
    owner = FileLock(home / "development.lock", timeout=0)
    try:
        owner.acquire()
    except Timeout:
        if marker.exists():
            existing = cast("dict[str, object]", json.loads(marker.read_text()))
            if existing.get("source") != str(source.resolve()):
                raise ValueError(
                    "Development home belongs to another checkout."
                ) from None
            print(f"Reusing development owner: {marker.read_text()}", flush=True)
        else:
            print(f"Development owner is starting: {home}", flush=True)
        activate = home / "desktop/activate"
        activate.parent.mkdir(exist_ok=True)
        activate.write_text("source-development", encoding="utf-8")
        return
    owned = False
    child: subprocess.Popen[str] | None = None
    child_owner: psutil.Process | None = None
    closing = threading.Event()
    commands: queue.Queue[str] = queue.Queue()
    quit_requested = threading.Event()
    session = DesktopSession(ApplicationRuntime(home), closing)
    endpoint = home / "backend.json"
    session.endpoint_changed = lambda url: publish_endpoint(home, url)
    previous_signals = {
        name: signal.getsignal(name) for name in (signal.SIGINT, signal.SIGTERM)
    }
    interruptible = True

    def request_quit(_number: int, _frame: object) -> None:
        if interruptible:
            raise KeyboardInterrupt
        queue_quit(commands, quit_requested)

    for name in previous_signals:
        signal.signal(name, request_quit)
    controller: threading.Thread | None = None
    try:
        claim_home(source, home)
        owned = True
        (
            session.runtime.instance.runtime_binding.coordination_root / RECORD_NAME
        ).unlink(missing_ok=True)
        logs = home / "logs"
        logs.mkdir(exist_ok=True)
        logging.basicConfig(filename=logs / "development.log", level=logging.INFO)
        print(f"Source: {source}\nHome (retained): {home}\nLogs: {logs}", flush=True)
        cache = development_home(source).parent / "resources"
        payload = prepare_application(session.runtime, source, cache)
        if not browser and policy is None:
            prepare_native_dependency(payload, cache)
        interruptible = False
        session.connected(session.runtime.start().base_url)
        child, session.ui_url = start_vite(source, endpoint, logs / "vite.log")
        child_owner = psutil.Process(child.pid)
        publish_ui(session, child_owner)
        report = {
            "source": str(source),
            "home": str(home),
            "ui": session.ui_url,
            "backend": session.base_url,
            "pid": os.getpid(),
        }
        write_state(marker, json.dumps(report, indent=2))
        backend_log = (
            session.runtime.instance.runtime_binding.coordination_root / "daemon.log"
        )
        print(
            f"UI: {session.ui_url}\nBackend: {session.base_url}\n"
            f"Backend log: {backend_log}\n"
            "r + Enter: safe restart; w: restart after idle; q/Ctrl-C: quit "
            "after idle; c: cancel wait.",
            flush=True,
        )

        def prepare() -> None:
            prepare_application(session.runtime, source, cache)

        def start() -> None:
            prepare()
            session.connected(session.runtime.start().base_url)

        controller = threading.Thread(
            target=control_session,
            args=(
                session,
                source,
                commands,
                start,
                child,
                logs / "vite.log",
                quit_requested,
            ),
            daemon=True,
        )
        controller.start()

        def read_commands() -> None:
            for line in sys.stdin:
                commands.put(line.strip().lower())

        threading.Thread(target=read_commands, daemon=True).start()
        if browser:
            print(
                "Internal browser inspection only; native operations require "
                "the desktop.",
                flush=True,
            )
            closing.wait()
        else:
            from scopecat_app.desktop import run as desktop_run

            desktop_run(
                home,
                session=session,
                prepare=prepare,
            )
    finally:
        interruptible = False
        if owned:
            (
                session.runtime.instance.runtime_binding.coordination_root / RECORD_NAME
            ).unlink(missing_ok=True)
            stop_when_idle(session.runtime)
        closing.set()
        if controller is not None:
            controller.join(timeout=5)
        if child is not None and child.poll() is None:
            terminate_validation_process_tree(child, owner=child_owner)
        for name, handler in previous_signals.items():
            signal.signal(name, handler)
        owner.release()


def main() -> None:
    policy = trial_development_policy()
    if policy is not None:
        from scopecat.trial_policy import validate_arguments

        validate_arguments(sys.argv[1:])
        if sys.argv[1:].count("--trial-development") != 1:
            raise ValueError(
                "Trial development requires exactly one explicit mode flag"
            )
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=policy is None)
    parser.add_argument(
        "--trial-development", action="store_true", help=argparse.SUPPRESS
    )
    homes = parser.add_mutually_exclusive_group()
    homes.add_argument(
        "--home", type=Path, help="Explicit retained blank development data home"
    )
    homes.add_argument(
        "--temporary",
        action="store_true",
        help="New blank home in the OS temp directory (retained, never auto-deleted)",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Internal browser inspection with the same backend; no native bridge",
    )
    args = cast("Arguments", cast("object", parser.parse_args()))
    if args.trial_development != (policy is not None):
        raise ValueError(
            "Trial development requires an explicit mode and marked interpreter"
        )
    if policy is not None and args.browser:
        raise ValueError("Trial development requires its native Windows host")
    source = source_root()
    home = args.home or (
        Path(tempfile.mkdtemp(prefix="scopecat-dev-"))
        if args.temporary
        else development_home(source)
    )
    trial_development_policy(source=source, home=home, entry=Path(__file__).absolute())
    if policy is not None:
        runtime = ApplicationRuntime(home)
        prepare_application(runtime, source, policy.home / "development/resources")
        if runtime.status().state != "stopped":
            raise ValueError("Trial development requires a prepared and stopped trial")
    try:
        if sys.platform == "darwin" and not args.browser:
            from scripts.app_build.dev_host import enter_host

            enter_host(source, home)
        elif sys.platform == "win32" and not args.browser:
            from scripts.app_build.dev_host_windows import enter_host

            enter_host(source, home)
        run(source, home, browser=args.browser)
    except KeyboardInterrupt:
        print("Development preparation cancelled; existing data retained.", flush=True)


if __name__ == "__main__":
    main()
