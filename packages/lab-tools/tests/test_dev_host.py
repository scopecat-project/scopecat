"""Thin host inputs, bundle sealing and exec handoff; not native UI acceptance."""

import json
import os
import plistlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lab_tools import dev, dev_host


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    source = tmp_path / "checkout"
    source.mkdir()
    python = source / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    library = tmp_path / "libpython.dylib"
    library.write_bytes(b"library")
    module = tmp_path / "builder/dev_host.py"
    module.parent.mkdir()
    module.write_text("builder")
    module.with_suffix(".c").write_text("launcher")
    icon = module.parent / "icons/Scopecat.icns"
    icon.parent.mkdir()
    icon.write_bytes(b"icon")
    monkeypatch.setattr(dev_host, "__file__", str(module))
    monkeypatch.setattr(dev_host.sys, "executable", str(python))
    monkeypatch.setattr(dev_host.sys, "prefix", str(source / ".venv"))
    monkeypatch.setattr(dev_host, "shared_library", lambda: library)
    monkeypatch.setattr(
        dev_host.subprocess, "check_output", lambda *_a, **_k: "clang test"
    )
    return source, python, library, module, icon


def test_host_identity_ignores_regular_source_and_gui_edits(inputs):
    source, *_ = inputs
    first = dev_host.host_identity(source)
    (source / "app.py").write_text("changed source")
    (source / "App.tsx").write_text("changed GUI")
    assert dev_host.host_identity(source) == first


@pytest.mark.parametrize(
    "changed",
    ["python", "library", "icon", "launcher", "builder", "venv", "abi", "checkout"],
)
def test_host_inputs_invalidate_cache(inputs, monkeypatch, changed):
    source, python, library, module, icon = inputs
    first = dev_host.host_identity(source)
    paths = {
        "python": python,
        "library": library,
        "icon": icon,
        "launcher": module.with_suffix(".c"),
        "builder": module,
        "venv": source / ".venv/pyvenv.cfg",
    }
    if changed in paths:
        paths[changed].write_bytes(b"changed")
    elif changed == "abi":
        monkeypatch.setattr(
            dev_host.sysconfig, "get_config_var", lambda _name: "other-abi"
        )
    else:
        source = source.parent / "another-checkout"
    assert dev_host.host_identity(source) != first


def test_interpreter_symlink_path_is_not_resolved_away(inputs):
    source, python, *_ = inputs
    actual = python.parent / "actual-python"
    python.rename(actual)
    python.symlink_to(actual)
    identity = dev_host.host_identity(source)
    assert identity["python"] == str(python)
    assert identity["resolved_python"] == str(actual)


@pytest.fixture
def builder(inputs, tmp_path, monkeypatch):
    source, *_ = inputs
    commands = []
    monkeypatch.setattr(
        dev_host, "development_home", lambda _source: tmp_path / "worktree-id"
    )
    monkeypatch.setattr(dev_host, "check_interpreter", Mock())

    def run(command, **_kwargs):
        commands.append(command)
        if command[0] == "/usr/bin/clang":
            Path(command[-1]).write_bytes(b"Mach-O fixture")
        elif "--sign" in command:
            app = Path(command[-1])
            assert (app / "Contents/Resources/Scopecat.icns").read_bytes() == b"icon"
            with (app / "Contents/Info.plist").open("rb") as stream:
                info = plistlib.load(stream)
            assert info["CFBundleDisplayName"] == "Scopecat Dev"
            assert info["CFBundleIdentifier"] == "org.scopecat.development.worktree-id"
            assert info["CFBundleIconFile"] == "Scopecat.icns"
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(dev_host.subprocess, "run", run)
    return source, commands


def test_build_is_thin_seals_icon_and_reuses_without_recompile(builder):
    source, commands = builder
    executable = dev_host.build_host(source)
    assert dev_host.build_host(source) == executable
    assert sum(command[0] == "/usr/bin/clang" for command in commands) == 1
    app = executable.parents[2]
    assert {
        str(path.relative_to(app)) for path in app.rglob("*") if path.is_file()
    } == {
        "Contents/Info.plist",
        "Contents/MacOS/scopecat-dev",
        "Contents/Resources/Scopecat.icns",
    }
    assert not (app / "Contents/Resources/python").exists()


def test_changed_cache_is_rejected_without_overwrite(builder):
    source, _ = builder
    executable = dev_host.build_host(source)
    executable.write_bytes(b"changed")
    with pytest.raises(ValueError, match="cache changed"):
        dev_host.build_host(source)
    assert executable.read_bytes() == b"changed"


def test_rebuild_retains_previous_host_and_worktree_identity(builder, inputs):
    source, _ = builder
    first = dev_host.build_host(source)
    inputs[-1].write_bytes(b"icon")  # Same content keeps the cached host.
    assert dev_host.build_host(source) == first
    inputs[2].write_bytes(b"changed-library")
    second = dev_host.build_host(source)
    assert second != first
    assert first.is_file()
    one = plistlib.loads((first.parents[1] / "Info.plist").read_bytes())
    two = plistlib.loads((second.parents[1] / "Info.plist").read_bytes())
    assert one["CFBundleIdentifier"] == two["CFBundleIdentifier"]


def test_probe_rejects_escape_from_venv(inputs, monkeypatch):
    source, *_ = inputs
    identity = dev_host.host_identity(source)
    monkeypatch.setattr(
        dev_host.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [identity["python"], "/base-python", identity["base_prefix"]]
            ),
            stderr="",
        ),
    )
    with pytest.raises(ValueError, match="preserve the selected Python"):
        dev_host.check_interpreter(Path("host"), identity)


def test_exec_preserves_process_terminal_and_passes_one_home(inputs, monkeypatch):
    source, python, *_ = inputs
    executable = source / "host/Scopecat Dev.app/Contents/MacOS/scopecat-dev"
    monkeypatch.delenv(dev_host.MARKER, raising=False)
    monkeypatch.setenv("PYTHONHOME", "unwanted")
    monkeypatch.setattr(dev_host, "build_host", lambda _source: executable)
    execute = Mock()
    monkeypatch.setattr(dev_host.os, "execve", execute)
    home = source / "retained blank home"
    dev_host.enter_host(source, home)
    path, arguments, environment = execute.call_args.args
    assert path == executable
    assert arguments == [str(executable), "-m", "lab_tools.dev", "--home", str(home)]
    assert "PYTHONHOME" not in environment
    marker = json.loads(environment[dev_host.MARKER])
    assert marker["pid"] == os.getpid()
    assert marker["python"] == str(python)
    assert marker["host"] == str(executable)

    monkeypatch.setenv(dev_host.MARKER, environment[dev_host.MARKER])
    monkeypatch.setattr(
        dev_host.psutil, "Process", lambda: SimpleNamespace(exe=lambda: str(executable))
    )
    execute.reset_mock()
    dev_host.enter_host(source, home)
    execute.assert_not_called()
    marker["prefix"] = "/escaped-prefix"
    monkeypatch.setenv(dev_host.MARKER, json.dumps(marker))
    with pytest.raises(ValueError, match="refusing recursive startup"):
        dev_host.enter_host(source, home)
    execute.assert_not_called()


@pytest.mark.parametrize(
    ("platform", "browser", "host_calls"),
    [
        ("darwin", False, 1),
        ("darwin", True, 0),
        ("win32", False, 0),
        ("linux", False, 0),
    ],
)
def test_entry_selects_host_before_application_owner(
    tmp_path, monkeypatch, platform, browser, host_calls
):
    events = []
    monkeypatch.setattr(dev.sys, "platform", platform)
    monkeypatch.setattr(
        dev.sys,
        "argv",
        ["dev", "--home", str(tmp_path), *(["--browser"] if browser else [])],
    )
    monkeypatch.setattr(dev, "source_root", lambda: tmp_path)
    monkeypatch.setattr(dev_host, "enter_host", lambda *_a: events.append("host"))
    monkeypatch.setattr(dev, "run", lambda *_a, **_k: events.append("owner"))
    dev.main()
    assert events == [*(["host"] if host_calls else []), "owner"]
