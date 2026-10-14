"""Author resource projection retains identity but never the offline runtime payload."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lab_tools import author_python, author_resources, bundle


@pytest.fixture
def delivery(tmp_path):
    root = tmp_path / "full"
    for folder in ("gui", "wheels", "toolchain"):
        (root / folder).mkdir(parents=True)
    (root / "gui/index.html").write_text("GUI")
    for name in (
        "scopecat",
        "scopecat_server",
        "scopecat_quantum",
        "scopecat_testkit",
        "numpy",
    ):
        (root / "wheels" / f"{name}-1-py3-none-any.whl").write_text(name)
    (root / "toolchain/python.tar").write_bytes(b"runtime")
    (root / "toolchain/uv").write_bytes(b"uv")
    files = bundle.inventory(root, ("gui", "wheels", "toolchain"))
    for name in ("requirements.lock", "dependencies.lock", "build.lock", "install.py"):
        (root / name).write_text("")
        files[name] = bundle.file_hash(root / name)
    (root / bundle.MANIFEST).write_text(
        json.dumps(
            {
                "format": 1,
                "target": bundle.target_identity(),
                "sources": {"public": "commit"},
                "runtime": {},
                "files": files,
                "public_version": "0.2.0",
                "build_number": 1,
            }
        )
    )
    return root


def test_projection_has_own_identity_and_keeps_full_delivery(delivery, tmp_path):
    before = (delivery / bundle.MANIFEST).read_bytes()
    resource = author_resources.build(delivery, tmp_path / "author")
    document = author_resources.verify_resources(resource)
    assert document["format"] == 2
    assert document["sources"] == {"public": "commit"}
    assert {p.name for p in (resource / "wheels").iterdir()} == {
        "scopecat-1-py3-none-any.whl",
        "scopecat_server-1-py3-none-any.whl",
    }
    assert not (resource / "toolchain/python.tar").exists()
    assert not (resource / "install.py").exists()
    assert (delivery / bundle.MANIFEST).read_bytes() == before
    bundle.verify_bundle(delivery)
    with pytest.raises(ValueError, match="清单版本"):
        bundle.install_bundle(resource, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()
    (resource / "author.json").write_text("modified")
    with pytest.raises(ValueError, match="被修改"):
        author_resources.verify_resources(resource)


@pytest.mark.parametrize(
    "extra", ["toolchain/python.tar", "wheels/numpy-1.whl", "build.lock"]
)
def test_extra_delivery_files_are_rejected(delivery, tmp_path, extra):
    resource = author_resources.build(delivery, tmp_path / "author")
    (resource / extra).write_text("unexpected")
    with pytest.raises(ValueError, match="多余"):
        author_resources.verify_resources(resource)


@pytest.fixture
def selection(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nrequires-python=">=3.14,<3.15"\n'
    )
    resource = tmp_path / "resources"
    resource.mkdir()
    (resource / "author.json").write_text('{"python":"3.14.7"}')
    app_python = tmp_path / "application/bin/python"
    runtime = SimpleNamespace(installation=lambda: SimpleNamespace(python=app_python))
    monkeypatch.delenv("UV_PYTHON", raising=False)
    return runtime, source, resource


def test_uv_manages_pinned_python_without_shell_changes(
    selection, monkeypatch, tmp_path
):
    runtime, source, resource = selection
    calls = []
    python = tmp_path / "managed/bin/python"

    def run(command):
        calls.append(command)
        if "-c" in command:
            return json.dumps(
                {
                    "version": "3.14.7",
                    "base": str(python.parent.parent),
                    "executable": str(python),
                }
            )
        if "find" in command and len(calls) == 1:
            raise ValueError("not installed")
        return str(python)

    monkeypatch.setattr(author_python, "run", run)
    assert author_python.resolve(runtime, source, resource) == python
    installation = next(c for c in calls if "install" in c)
    assert installation[3:] == ["3.14.7", "--no-bin", "--no-registry"]
    assert "--managed-python" in calls[0]
    assert not (source / ".scopecat-python").exists()


@pytest.mark.parametrize(
    "mode", ["incompatible", "application", "based-on-application"]
)
def test_explicit_python_is_checked_before_use(selection, monkeypatch, tmp_path, mode):
    runtime, source, resource = selection
    python = tmp_path / "user/bin/python"
    app = runtime.installation().python
    monkeypatch.setattr(
        author_python,
        "run",
        lambda _c: json.dumps(
            {
                "version": "3.13.9" if mode == "incompatible" else "3.14.7",
                "base": str(
                    app.parent.parent
                    if mode == "based-on-application"
                    else python.parent.parent
                ),
                "executable": str(app if mode == "application" else python),
            }
        ),
    )
    with pytest.raises(ValueError, match=r"不满足|应用内部"):
        author_python.resolve(runtime, source, resource, python=python)


def test_offline_missing_python_never_downloads(selection, monkeypatch):
    runtime, source, resource = selection
    calls = []

    def missing(command):
        calls.append(command)
        raise ValueError("missing")

    monkeypatch.setattr(author_python, "run", missing)
    with pytest.raises(ValueError, match="离线缓存"):
        author_python.resolve(runtime, source, resource, offline=True)
    assert len(calls) == 1 and "find" in calls[0]


def test_workspace_pin_overrides_recipe_and_latest_is_rejected(selection, monkeypatch):
    runtime, source, resource = selection
    pin = source / ".python-version"
    pin.write_text("3.14.8\n")
    calls = []
    monkeypatch.setattr(
        author_python, "run", lambda c: calls.append(c) or "/managed/python"
    )
    monkeypatch.setattr(author_python, "check", lambda _r, _w, p: p)
    assert author_python.resolve(runtime, source, resource) == Path("/managed/python")
    assert calls[0][3] == "3.14.8"
    pin.write_text("latest")
    with pytest.raises(ValueError, match="latest"):
        author_python.resolve(runtime, source, resource)
    assert len(calls) == 1
