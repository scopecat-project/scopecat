"""The Mac maintenance console survives relocation without borrowing PATH Python."""

import json
import os
import site
import subprocess
import venv
from pathlib import Path

import pytest

from lab_tools.native_package import _write_macos_console

pytestmark = pytest.mark.skipif(os.name == "nt", reason="macOS POSIX console")


@pytest.fixture
def console_runtime(tmp_path):
    staging = tmp_path / ".native-build-original"
    python = staging / "Scopecat.app/Contents/Resources/python"
    venv.EnvBuilder(with_pip=False).create(python)
    library = next((python / "lib").glob("python*/site-packages"))
    # Reuse installed test dependencies while keeping a distinct executable/prefix.
    (library / "test-dependencies.pth").write_text(
        "import site; "
        + "; ".join(f"site.addsitedir({path!r})" for path in site.getsitepackages())
        + "\n"
    )
    old_console = python / "bin/scopecat"
    old_console.write_text(f"#!{python / 'bin/python3'}\nraise SystemExit(99)\n")
    _write_macos_console(python)
    moved = tmp_path / "Moved 中文 application"
    staging.rename(moved)
    assert not staging.exists()
    python = moved / python.relative_to(staging)
    home = tmp_path / "User 中文 home"
    environment = dict(os.environ, PATH="", HOME=str(home), PYTHONPATH="/missing")
    return python, environment


def run_console(python, environment, *arguments):
    return subprocess.run(  # noqa: S603 - disposable test runtime
        [str(python / "bin/scopecat"), *arguments],
        env=environment,
        cwd=python.parent,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_relocated_console_runs_real_help_and_status(console_runtime, tmp_path):
    python, environment = console_runtime

    def contents():
        return {
            path.relative_to(python): path.read_bytes()
            for path in python.rglob("*")
            if path.is_file()
        }

    before = contents()
    help_result = run_console(python, environment, "--help")
    assert help_result.returncode == 0, help_result.stderr
    assert "scopecat" in help_result.stdout and "app" in help_result.stdout
    home = tmp_path / "Selected 中文 home with spaces"
    result = run_console(python, environment, "app", "--home", str(home))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"state": "not-installed", "home": str(home)}
    invalid = run_console(python, environment, "app", "--unknown-option")
    assert invalid.returncode == 2
    assert not Path(environment["HOME"]).exists()
    assert contents() == before


def test_console_forwards_arguments_identity_and_exit_code(console_runtime):
    python, environment = console_runtime
    library = next((python / "lib").glob("python*/site-packages"))
    module = library / "lab_tools"
    module.mkdir()
    (module / "__init__.py").write_text("")
    (module / "public_cli.py").write_text(
        "import json, sys\n"
        "print(json.dumps([sys.executable, sys.prefix, sys.argv[1:]]))\n"
        "raise SystemExit(23)\n"
    )
    arguments = [
        "",
        "中文 path with spaces",
        "single'quote",
        'double"quote',
        "$HOME",
        "*",
    ]
    result = run_console(python, environment, *arguments)
    assert result.returncode == 23, result.stderr
    executable, prefix, forwarded = json.loads(result.stdout)
    assert Path(executable) == python / "bin/python3"
    assert Path(prefix) == python
    assert forwarded == arguments
    # A missing bundled interpreter must fail, even if a system Python exists.
    (python / "bin/python3").unlink()
    missing = run_console(python, dict(environment, PATH=os.defpath), "--help")
    # macOS /bin/sh reports 126; Linux dash reports 127 for this exec failure.
    assert missing.returncode in (126, 127)
    assert str(python / "bin/python3") in missing.stderr


def test_build_replaces_console_before_signing_and_rename(tmp_path, monkeypatch):
    import tarfile

    from lab_tools import cocoa_dependency, macos_signing, native_package

    source = tmp_path / "delivery"
    (source / "toolchain").mkdir(parents=True)
    (source / "bundle.json").write_text(
        json.dumps({"public_version": "0.2.0", "build_number": 1})
    )
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "lib").mkdir()
    (runtime / "bin/python3").write_text("runtime")
    (runtime / "lib/libpython3.14.dylib").write_text("library")
    with tarfile.open(source / "toolchain/python.tar", "w") as archive:
        archive.add(runtime / "bin", arcname="bin")
        archive.add(runtime / "lib", arcname="lib")
    monkeypatch.setattr(native_package.sys, "platform", "darwin")
    monkeypatch.setattr(native_package, "resolve_delivery", lambda path: path)
    monkeypatch.setattr(native_package, "verify_bundle", lambda _: None)
    monkeypatch.setattr(cocoa_dependency, "verify_wheel", lambda _: None)
    installed = []

    def run(command):
        if command[1:3] == ["pip", "install"]:
            python = Path(command[command.index("--python") + 1])
            entry = python.with_name("scopecat")
            entry.write_text(f"#!{python}\n")
            installed.append(entry)

    signed = []

    def sign(app):
        entry = app / "Contents/Resources/python/bin/scopecat"
        assert entry == installed[0]
        assert entry.read_text().startswith("#!/bin/sh\n")
        assert ".native-build-" not in entry.read_text()
        assert entry.stat().st_mode & 0o111 == 0o111
        signed.append(app)

    monkeypatch.setattr(native_package, "_run", run)
    monkeypatch.setattr(macos_signing, "sign", sign)
    destination = tmp_path / "Final 中文 Scopecat.app"
    assert native_package.build(source, destination) == destination
    assert len(signed) == 1 and not signed[0].exists()
    assert not installed[0].exists()
    assert (destination / "Contents/Resources/python/bin/scopecat").is_file()
