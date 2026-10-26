"""Author resource projection retains identity but never the offline runtime payload."""

import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from scripts.app_build import author_resources as resource_builder
from scripts.app_build import installer
from scripts.app_build.author_resources import build as build_author_resources

from scopecat_app import author_python, author_resources, bundle
from scopecat_app.application_runtime import ApplicationRuntime

type Selection = tuple[Mock, Path, Path]


@pytest.fixture
def delivery(tmp_path: Path) -> Path:
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


def test_projection_has_own_identity_and_keeps_full_delivery(
    delivery: Path, tmp_path: Path
):
    before = (delivery / bundle.MANIFEST).read_bytes()
    resource = build_author_resources(delivery, tmp_path / "author")
    document = author_resources.verify_resources(resource)
    assert document["format"] == 2
    assert document["sources"] == {"public": "commit"}
    metadata = json.loads((resource / bundle.MANIFEST).read_text())
    assert metadata["public_version"] == "0.2.0"
    assert metadata["build_number"] == 1
    assert {p.name for p in (resource / "wheels").iterdir()} == {
        "scopecat-1-py3-none-any.whl",
        "scopecat_server-1-py3-none-any.whl",
    }
    assert not (resource / "toolchain/python.tar").exists()
    assert not (resource / "install.py").exists()
    assert (delivery / bundle.MANIFEST).read_bytes() == before
    bundle.verify_bundle(delivery)
    with pytest.raises(ValueError, match="清单版本"):
        installer.install_bundle(resource, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()
    (resource / "author.json").write_text("modified")
    with pytest.raises(ValueError, match="被修改"):
        author_resources.verify_resources(resource)


@pytest.mark.parametrize(
    "extra", ["toolchain/python.tar", "wheels/numpy-1.whl", "build.lock"]
)
def test_extra_delivery_files_are_rejected(delivery: Path, tmp_path: Path, extra):
    resource = build_author_resources(delivery, tmp_path / "author")
    (resource / extra).write_text("unexpected")
    with pytest.raises(ValueError, match="多余"):
        author_resources.verify_resources(resource)


@pytest.fixture
def materials(tmp_path: Path) -> resource_builder.AuthorInputs:
    root = tmp_path / "materials"
    (root / "gui/assets").mkdir(parents=True)
    (root / "wheels").mkdir()
    (root / "gui/index.html").write_text("GUI")
    (root / "gui/assets/main.js").write_text("asset")
    for package in author_resources.PACKAGES:
        (root / "wheels" / f"{package}-1-py3-none-any.whl").write_text(package)
    (root / "requirements.lock").write_text("dependency==1 --hash=sha256:example")
    files = bundle.inventory(root, ("gui", "wheels"))
    files["requirements.lock"] = bundle.file_hash(root / "requirements.lock")
    return resource_builder.AuthorInputs(
        root, bundle.target_identity(), {"public": "commit+dirty"}, files
    )


@pytest.mark.parametrize("with_tool", [False, True])
def test_explicit_inputs_need_no_legacy_delivery(
    materials: resource_builder.AuthorInputs, tmp_path: Path, with_tool: bool
):
    if with_tool:
        (materials.root / "toolchain").mkdir()
        for name in ("uv.exe" if os.name == "nt" else "uv", "LICENSE-MIT"):
            path = materials.root / "toolchain" / name
            path.write_text(name)
            materials.files[f"toolchain/{name}"] = bundle.file_hash(path)
    before = dict(materials.files)
    resource = build_author_resources(materials, tmp_path / "author")
    document = author_resources.verify_resources(resource)
    assert document["sources"] == materials.sources
    assert document["target"] == materials.target
    assert document["runtime"] == {}
    assert document["format"] == 2
    assert {k: v for k, v in document["files"].items() if k != "author.json"} == before
    assert materials.files == before
    assert not (materials.root / bundle.MANIFEST).exists()
    assert not (resource / "install.py").exists()


@pytest.mark.parametrize(
    "name",
    [
        "gui/index.html",
        "gui/assets/main.js",
        "requirements.lock",
        "wheels/scopecat-1-py3-none-any.whl",
        "wheels/scopecat_server-1-py3-none-any.whl",
    ],
)
@pytest.mark.parametrize("damage", ["missing", "modified", "symlink"])
def test_explicit_inputs_reject_bad_materials_before_output(
    materials: resource_builder.AuthorInputs, tmp_path: Path, name: str, damage: str
):
    path = materials.root / name
    if damage == "modified":
        path.write_text("tampered")
    else:
        path.unlink()
        if damage == "symlink":
            replacement = tmp_path / "replacement"
            replacement.write_text("tampered")
            path.symlink_to(replacement)
    destination = tmp_path / "author"
    with pytest.raises(ValueError, match=r"作者|符号链接"):
        build_author_resources(materials, destination)
    assert not destination.exists()


@pytest.mark.parametrize("folder", ["gui", "wheels", "toolchain"])
def test_explicit_inputs_reject_redirected_directories(
    materials: resource_builder.AuthorInputs, tmp_path: Path, folder: str
):
    path = materials.root / folder
    relocated = tmp_path / "relocated"
    if path.exists():
        path.rename(relocated)
    else:
        relocated.mkdir()
    path.symlink_to(relocated, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接"):
        build_author_resources(materials, tmp_path / "author")


@pytest.mark.parametrize(
    "field", ["platform", "machine", "python", "implementation", "free_threaded"]
)
def test_explicit_inputs_reject_target_mismatch(
    materials: resource_builder.AuthorInputs, tmp_path: Path, field: str
):
    inputs = replace(materials, target={**materials.target, field: "different"})
    with pytest.raises(ValueError, match="target"):
        build_author_resources(inputs, tmp_path / "author")
    assert not (tmp_path / "author").exists()


@pytest.mark.parametrize("damage", ["source", "hash", "extra-hash", "duplicate"])
def test_explicit_inputs_require_identity_and_exact_material_inventory(
    materials: resource_builder.AuthorInputs, tmp_path: Path, damage: str
):
    if damage == "source":
        materials = replace(materials, sources={})
    elif damage == "hash":
        materials.files["requirements.lock"] = "0" * 64
    elif damage == "extra-hash":
        materials.files["../outside"] = "0" * 64
    else:
        (materials.root / "wheels/scopecat-2-py3-none-any.whl").write_text("duplicate")
    with pytest.raises(ValueError, match=r"作者|符号链接"):
        build_author_resources(materials, tmp_path / "author")
    assert not (tmp_path / "author").exists()


@pytest.mark.parametrize("damage", ["missing", "modified"])
def test_optional_tool_is_verified_when_recorded(
    materials: resource_builder.AuthorInputs, tmp_path: Path, damage: str
):
    (materials.root / "toolchain").mkdir()
    name = "toolchain/uv.exe" if os.name == "nt" else "toolchain/uv"
    path = materials.root / name
    path.write_text("verified uv")
    materials.files[name] = bundle.file_hash(path)
    if damage == "missing":
        path.unlink()
    else:
        path.write_text("modified uv")
    with pytest.raises(ValueError, match="哈希清单"):
        build_author_resources(materials, tmp_path / "author")
    assert not (tmp_path / "author").exists()


def test_copy_does_not_launder_changed_material_hashes(
    materials: resource_builder.AuthorInputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    copy = shutil.copy2

    def changed_copy(source: Path, destination: Path) -> Path | str:
        result = copy(source, destination)
        Path(destination).write_text("changed during copy")
        return result

    monkeypatch.setattr(shutil, "copy2", changed_copy)
    with pytest.raises(ValueError, match="预期哈希"):
        build_author_resources(materials, tmp_path / "author")
    assert not (tmp_path / "author" / bundle.MANIFEST).exists()


def test_legacy_adapter_still_checks_unconsumed_files(delivery: Path, tmp_path: Path):
    (delivery / "install.py").write_text("tampered")
    with pytest.raises(ValueError, match="被修改"):
        build_author_resources(delivery, tmp_path / "author")
    assert not (tmp_path / "author").exists()


@pytest.fixture
def selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Selection:
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nrequires-python=">=3.14,<3.15"\n'
    )
    resource = tmp_path / "resources"
    resource.mkdir()
    (resource / "author.json").write_text('{"python":"3.14.7"}')
    app_python = tmp_path / "application/bin/python"
    runtime = Mock(spec=ApplicationRuntime)
    runtime.installation.return_value = SimpleNamespace(python=app_python)
    monkeypatch.delenv("UV_PYTHON", raising=False)
    return runtime, source, resource


def test_uv_manages_pinned_python_without_shell_changes(
    selection: Selection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
def test_explicit_python_is_checked_before_use(
    selection: Selection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode
):
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


def test_offline_missing_python_never_downloads(
    selection: Selection, monkeypatch: pytest.MonkeyPatch
):
    runtime, source, resource = selection
    calls = []

    def missing(command):
        calls.append(command)
        raise ValueError("missing")

    monkeypatch.setattr(author_python, "run", missing)
    with pytest.raises(ValueError, match="离线缓存"):
        author_python.resolve(runtime, source, resource, offline=True)
    assert len(calls) == 1 and "find" in calls[0]


def test_unavailable_python_download_explains_explicit_choice_without_fallback(
    selection: Selection, monkeypatch: pytest.MonkeyPatch
):
    runtime, source, resource = selection
    retained = source / ".venv" / "retained.txt"
    retained.parent.mkdir()
    retained.write_text("existing client")
    calls = []

    def missing(command):
        calls.append(command)
        raise ValueError("No download found for request")

    monkeypatch.setattr(author_python, "run", missing)
    with pytest.raises(
        ValueError, match=r"无法获取所需的独立 Python 3\.14\.7"
    ) as failure:
        author_python.resolve(runtime, source, resource)
    assert "--python" in str(failure.value)
    assert "UV_PYTHON" in str(failure.value)
    assert "No download found" in str(failure.value)
    assert [command[2] for command in calls] == ["find", "install"]
    assert calls[1][3:] == ["3.14.7", "--no-bin", "--no-registry"]
    assert retained.read_text() == "existing client"


def test_workspace_pin_overrides_recipe_and_latest_is_rejected(
    selection: Selection, monkeypatch: pytest.MonkeyPatch
):
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


def test_recipe_uses_probed_runtime_version_and_rejects_target_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = bundle.target_identity()
    info = {"version": "3.14.99", "target": target}
    monkeypatch.setattr(
        resource_builder.subprocess, "check_output", lambda *_a, **_kw: json.dumps(info)
    )
    recipe = resource_builder._recipe(tmp_path / "packaged-python", target)
    assert recipe["python"] == "3.14.99" + (
        "t" if target["free_threaded"] == "True" else ""
    )
    info["target"] = {**target, "implementation": "different"}
    with pytest.raises(ValueError, match="target"):
        resource_builder._recipe(tmp_path / "packaged-python", target)


def test_python_identity_tracks_same_path_binary_library_and_abi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    binary = tmp_path / "python"
    library = tmp_path / "libpython"
    binary.write_bytes(b"first")
    library.write_bytes(b"library")
    info = {
        "binary": str(binary),
        "library": str(library),
        "version": "3.14.7",
        "abi": "cpython-314",
    }
    monkeypatch.setattr(author_python, "run", lambda _c: json.dumps(info))
    first = author_python.identity(binary)
    binary.write_bytes(b"replacement")
    second = author_python.identity(binary)
    assert first != second
    library.write_bytes(b"updated library")
    third = author_python.identity(binary)
    assert second != third
    info["abi"] = "cpython-314t"
    assert author_python.identity(binary) != third
