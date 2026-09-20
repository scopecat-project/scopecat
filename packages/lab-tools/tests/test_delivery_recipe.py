"""Locked laboratory recipes reuse the application delivery pipeline."""

import json
import tomllib
import zipfile
from pathlib import Path

import pytest

from lab_tools import delivery
from lab_tools.bundle import MANIFEST, verify_bundle


def project(path: Path, name: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text(f'[project]\nname="{name}"\nversion="1.0"\n')
    (path / "uv.lock").write_text(f"# {name} retained lock\n")


@pytest.fixture
def recipe(tmp_path):
    project(tmp_path, "my-adapter")
    public = tmp_path / "public"
    project(public, "public-workspace")
    for directory, name in (
        ("scopecat", "scopecat"),
        ("scopecat-server", "scopecat-server"),
        ("scopecat-instruments", "scopecat-instruments"),
        ("lab-tools", "scopecat-lab-tools"),
        ("lab-teaching", "scopecat-lab-teaching"),
    ):
        project(public / "packages" / directory, name)
    installer = public / "packages/lab-tools/src/lab_tools/bundle.py"
    installer.parent.mkdir(parents=True)
    installer.write_text("# maintained installer\n")
    path = tmp_path / "delivery.toml"
    path.write_text(
        '[delivery]\nlock_project="."\npublic_source="public"\n'
        'dependency_group="lab-delivery"\ninclude_project=true\n'
        'packages=[".","public/packages/scopecat", "public/packages/scopecat-server", '
        '"public/packages/scopecat-instruments", "public/packages/lab-tools", '
        '"public/packages/lab-teaching"]\n'
    )
    return path


@pytest.fixture
def build_tools(monkeypatch):
    calls = []

    def run(command, *, cwd):
        calls.append((command, cwd))
        if command[1] == "export":
            Path(command[command.index("--output-file") + 1]).write_text(
                "# locked dependencies\n"
            )
        elif command[1] == "build":
            package = Path(command[-1])
            metadata = tomllib.loads((package / "pyproject.toml").read_text())
            name = metadata["project"]["name"]
            module = next(
                (module for module, dist in delivery.MODULES.items() if dist == name),
                name.replace("-", "_"),
            )
            output = Path(command[command.index("--out-dir") + 1])
            with zipfile.ZipFile(
                output / f"{name.replace('-', '_')}-1.0-py3-none-any.whl", "w"
            ) as wheel:
                wheel.writestr(
                    f"{name}.dist-info/METADATA", f"Name: {name}\nVersion: 1.0\n"
                )
                wheel.writestr(f"{module}/__init__.py", "# installed bytes\n")

    monkeypatch.setattr(delivery, "run", run)
    monkeypatch.setattr(delivery.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        delivery.subprocess,
        "check_output",
        lambda command, **_: "" if command[1] == "status" else "fixture-identity\n",
    )
    return calls


def test_recipe_build_uses_locked_lab_and_public_toolchain(
    recipe, tmp_path, build_tools
):
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>workbench</html>")
    result = delivery.build_delivery(
        tmp_path / "output", recipe=recipe, gui=gui, release=True
    )
    calls = build_tools
    export, cwd = next(item for item in calls if item[0][1] == "export")
    assert cwd == tmp_path
    assert "--locked" in export and "--no-emit-local" in export
    assert export[export.index("--group") + 1] == "lab-delivery"
    assert "--no-default-groups" in export
    download, cwd = next(item for item in calls if "download" in item[0])
    assert cwd == tmp_path / "public"
    assert {"--require-hashes", "--no-deps", "--only-binary=:all:"} <= set(download)
    assert (result / "build.lock").read_bytes() == (tmp_path / "uv.lock").read_bytes()
    assert (
        "my-adapter==1.0 --hash=sha256:" in (result / "requirements.lock").read_text()
    )
    metadata = json.loads((result / MANIFEST).read_text())
    assert set(metadata["sources"]) == {"public", "lab"}
    assert metadata["recipe_sha256"] == delivery.file_hash(recipe)
    verify_bundle(result)


def test_default_build_includes_instrument_dependency(recipe, tmp_path, build_tools):
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>workbench</html>")
    result = delivery.build_delivery(
        tmp_path / "output", source=tmp_path / "public", gui=gui
    )
    lock = (result / "requirements.lock").read_text()
    assert "scopecat-instruments==1.0 --hash=sha256:" in lock
    assert set(json.loads((result / MANIFEST).read_text())["sources"]) == {"public"}


@pytest.mark.parametrize("bad", ["../outside", "/absolute", "C:\\outside"])
def test_recipe_paths_cannot_escape(recipe, bad):
    text = recipe.read_text().replace('lock_project="."', f"lock_project='{bad}'")
    recipe.write_text(text)
    with pytest.raises(ValueError, match="relative directories"):
        delivery.load_recipe(recipe)


def test_duplicate_package_names_fail_before_build(recipe, tmp_path, build_tools):
    (tmp_path / "public/packages/lab-tools/pyproject.toml").write_text(
        '[project]\nname="my_adapter"\n'
    )
    with pytest.raises(ValueError, match="duplicate distribution"):
        delivery.build_delivery(tmp_path / "output", recipe=recipe)
    assert not (tmp_path / "output").exists()
    assert build_tools == []


def test_duplicate_wheel_distribution_is_rejected(tmp_path):
    for filename, name in (("one.whl", "same-package"), ("two.whl", "same_package")):
        with zipfile.ZipFile(tmp_path / filename, "w") as wheel:
            wheel.writestr("same.dist-info/METADATA", f"Name: {name}\nVersion: 1\n")
    with pytest.raises(ValueError, match="multiple wheels"):
        delivery._unique_wheels(tmp_path)
