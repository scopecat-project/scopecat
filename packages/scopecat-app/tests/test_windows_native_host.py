"""Compile and exercise the actual Windows host without opening a WebView."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.app_build import dev_host_windows

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows native host integration",
)


def test_host_preserves_process_identity_and_python_subprocesses(tmp_path):
    app = tmp_path / "Relocated 中文 app"
    python = app / "resources/python"
    python.mkdir(parents=True)
    base = Path(sys.base_prefix)
    for source in (*base.glob("*.dll"), base / "python.exe"):
        shutil.copy2(source, python / source.name)
    shutil.copytree(base / "DLLs", python / "DLLs")
    shutil.copytree(
        base / "Lib",
        python / "Lib",
        ignore=shutil.ignore_patterns("site-packages", "__pycache__"),
    )
    executable = app / "Scopecat.exe"
    source = Path(__file__).resolve().parents[3] / "scripts/app_build/native_launcher.c"
    compiler, environment = dev_host_windows.compiler_environment()
    subprocess.run(  # noqa: S603 - compile the repository host into a disposable path
        [
            compiler,
            "/nologo",
            "/utf-8",
            "/O2",
            "/MT",
            str(source),
            f"/Fe:{executable}",
            f"/Fo:{tmp_path / 'host.obj'}",
            "shell32.lib",
            "user32.lib",
            "/link",
            "/SUBSYSTEM:WINDOWS",
        ],
        check=True,
        env=environment,
        capture_output=True,
        timeout=60,
    )
    (app / "resources/bootstrap.py").write_text(
        "import ctypes, json, subprocess, sys\n"
        "from pathlib import Path\n"
        "name = ctypes.create_unicode_buffer(32768)\n"
        "assert ctypes.windll.kernel32.GetModuleFileNameW(None, name, len(name))\n"
        "identity = ctypes.c_wchar_p()\n"
        "get_id = ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID\n"
        "assert get_id(ctypes.byref(identity)) == 0\n"
        "child = subprocess.check_output([sys.executable, '-I', '-X', 'utf8', '-c',\n"
        "    'import sys; print(sys.executable)'], encoding='utf-8').strip()\n"
        "Path(sys.argv[1]).write_text(json.dumps({\n"
        "    'host': name.value, 'identity': identity.value,\n"
        "    'python': sys.executable, 'child': child, 'args': sys.argv[2:],\n"
        "    'isolated': sys.flags.isolated,\n"
        "    'bytecode': sys.dont_write_bytecode}))\n"
        "sys.exit(23)\n",
        encoding="utf-8",
    )
    report = tmp_path / "结果 with spaces.json"
    args = ["--check-result", 'quoted "中文" value', "trailing\\"]
    completed = subprocess.run(  # noqa: S603 - the freshly compiled test host
        [str(executable), str(report), *args],
        env=dict(os.environ, PATH="", PYTHONHOME=str(tmp_path / "wrong")),
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert completed.returncode == 23, completed.stderr
    state = json.loads(report.read_text())
    assert Path(state["host"]) == executable
    assert state["identity"] == "org.scopecat.desktop"
    assert Path(state["python"]) == python / "python.exe"
    assert state["child"] == state["python"]
    assert state["args"] == args
    assert state["isolated"] == 1 and state["bytecode"] is True


def test_development_host_preserves_venv_and_editable_source(tmp_path, monkeypatch):
    source = tmp_path / "checkout 中文"
    source.mkdir()
    monkeypatch.setattr(
        dev_host_windows, "development_home", lambda _: tmp_path / "cache"
    )
    executable = dev_host_windows.build_host(source)
    module = source / "editable_probe.py"
    module.write_text("VALUE = 7\n")
    probe = (
        "import ctypes, json, subprocess, sys, editable_probe;"
        "name=ctypes.create_unicode_buffer(32768);"
        "assert ctypes.windll.kernel32.GetModuleFileNameW(None,name,len(name));"
        "identity=ctypes.c_wchar_p();"
        "get_id=ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID;"
        "assert get_id(ctypes.byref(identity))==0;"
        "child=subprocess.check_output([sys.executable,'-E','-c',"
        "'import sys; print(sys.prefix)'],text=True).strip();"
        "print(json.dumps([name.value,identity.value,sys.executable,sys.prefix,child,editable_probe.VALUE,sys.argv[1:]]));"
        "sys.exit(23)"
    )
    for value in (7, 12345):
        module.write_text(f"VALUE = {value}\n")
        assert dev_host_windows.build_host(source) == executable
        result = subprocess.run(  # noqa: S603 - freshly compiled source host
            [str(executable), "-E", "-B", "-c", probe, '中文 "quote"', "trailing\\"],
            cwd=source,
            capture_output=True,
            text=True,
            timeout=30,
            env=dict(os.environ, PYTHONHOME=str(tmp_path / "wrong")),
        )
        assert result.returncode == 23, result.stderr
        observed = json.loads(result.stdout)
        assert Path(observed[0]) == executable
        assert observed[1] == "org.scopecat.development.cache"
        assert observed[2:6] == [sys.executable, sys.prefix, sys.prefix, value]
        assert observed[6] == ['中文 "quote"', "trailing\\"]
    repository = Path(__file__).resolve().parents[3]
    home = tmp_path / "unused-home"
    for module, options, expected in (
        ("scripts.dev", ["--home", str(home)], "--temporary"),
        ("scopecat_app.native_bootstrap", [], "--payload"),
    ):
        result = subprocess.run(  # noqa: S603 - freshly compiled repository host
            [str(executable), "-E", "-m", module, *options, "--help"],
            cwd=repository,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=dev_host_windows.clean_environment(),
        )
        assert result.returncode == 0, result.stderr
        assert expected in result.stdout
    assert not home.exists()
    executable.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="cache changed"):
        dev_host_windows.build_host(source)
