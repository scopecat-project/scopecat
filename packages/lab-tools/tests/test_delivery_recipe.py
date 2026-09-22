"""Locked laboratory recipes reuse the application delivery pipeline."""

import json
import shutil
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


def test_managed_recipe_build_uses_explicit_separate_public_checkout(
    recipe, tmp_path, build_tools
):
    external = tmp_path / "separate-checkout"
    shutil.move(str(tmp_path / "public"), external)
    document = tomllib.loads(recipe.read_text())["delivery"]
    local = tmp_path / "lab"
    project(local, "my-adapter")
    recipe = local / "delivery.toml"
    packages = [
        '"."',
        *(
            '{source="public", path=' + json.dumps(name.removeprefix("public/")) + "}"
            for name in document["packages"][1:]
        ),
    ]
    recipe.write_text(
        '[delivery]\nlock_project="."\ndependency_group="lab-delivery"\n'
        "include_project=true\npackages=[" + ",".join(packages) + "]\n"
    )
    with pytest.raises(ValueError, match="explicit --source"):
        delivery.load_recipe(recipe)
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>workbench</html>")
    result = delivery.build_managed_delivery(
        tmp_path / "output", recipe=recipe, source=external, gui=gui
    )
    plan = delivery.load_recipe(recipe, public_source=external)
    assert plan.public_source == external
    assert plan.lock_project == local
    assert plan.packages[0] == local
    assert all(item.is_relative_to(external) for item in plan.packages[1:])
    assert {
        Path(command[-1]) for command, _ in build_tools if command[1] == "build"
    } == set(plan.packages)
    verify_bundle(result)
    assert set(json.loads((result / MANIFEST).read_text())["sources"]) == {
        "public",
        "lab",
    }


def test_selected_public_root_rejects_package_escape_and_symlinks(recipe, tmp_path):
    public = tmp_path / "public"
    original = recipe.read_text()
    for bad in ("../outside", "/absolute", "C:\\outside"):
        recipe.write_text(
            original.replace(
                'packages=["."',
                'packages=[{source="public", path=' + json.dumps(bad) + "}",
            )
        )
        with pytest.raises(ValueError, match="relative directories"):
            delivery.load_recipe(recipe, public_source=public)
    escaped = public / "escaped"
    escaped.symlink_to(tmp_path, target_is_directory=True)
    recipe.write_text(
        original.replace('packages=["."', 'packages=[{source="public", path="escaped"}')
    )
    with pytest.raises(ValueError, match="local pyproject"):
        delivery.load_recipe(recipe, public_source=public)


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


def test_managed_build_retains_previous_success_on_failure_and_then_advances(
    recipe, tmp_path, build_tools, monkeypatch
):
    from lab_tools.bundle import CURRENT_DELIVERY, resolve_delivery, retain_bundle

    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>first</html>")
    home = tmp_path / "output"
    first = delivery.build_managed_delivery(home, recipe=recipe, gui=gui)
    selected = (home / CURRENT_DELIVERY).read_bytes()
    assert resolve_delivery(home) == first
    run = delivery.run

    def fail(*args, **kwargs):
        raise OSError("build failed")

    monkeypatch.setattr(delivery, "run", fail)
    with pytest.raises(OSError, match="build failed"):
        delivery.build_managed_delivery(home, recipe=recipe, gui=gui)
    assert (home / CURRENT_DELIVERY).read_bytes() == selected
    assert len(list((home / "builds").iterdir())) == 2
    assert resolve_delivery(home) == first
    monkeypatch.setattr(delivery, "run", run)
    (gui / "index.html").write_text("<html>second</html>")
    second = delivery.build_managed_delivery(home, recipe=recipe, gui=gui)
    assert second != first and first.is_dir()
    assert resolve_delivery(home) == second
    retained = retain_bundle(home, tmp_path / "application")
    assert (retained / "gui/index.html").read_text() == "<html>second</html>"
    # Explicit old artifacts remain selectable regardless of the moving pointer.
    assert resolve_delivery(first) == first


def test_managed_build_busy_fails_before_build(recipe, tmp_path, build_tools):
    from filelock import FileLock

    home = tmp_path / "output"
    home.mkdir()
    with (
        FileLock(home / ".build.lock"),
        pytest.raises(ValueError, match="构建正在进行"),
    ):
        delivery.build_managed_delivery(home, recipe=recipe)
    assert not (home / "builds").exists() and build_tools == []


def test_managed_selection_binds_manifest_and_rejects_escape(tmp_path, delivery):
    import shutil

    from lab_tools.bundle import CURRENT_DELIVERY, resolve_delivery

    home = tmp_path / "output"
    identity = "a" * 32
    artifact = home / "builds" / identity
    shutil.copytree(delivery, artifact)
    pointer = home / CURRENT_DELIVERY
    selection = {"format": 1, "build": identity, "manifest_sha256": "b" * 64}
    pointer.write_text(json.dumps(selection))
    with pytest.raises(ValueError, match="清单与构建选择不符"):
        resolve_delivery(home)
    selection["build"] = "../" + "a" * 29
    pointer.write_text(json.dumps(selection))
    with pytest.raises(ValueError, match="无效的当前交付"):
        resolve_delivery(home)


def test_unverified_completed_build_does_not_replace_current(
    recipe, tmp_path, build_tools, monkeypatch
):
    from lab_tools.bundle import resolve_delivery

    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>workbench</html>")
    home = tmp_path / "output"
    first = delivery.build_managed_delivery(home, recipe=recipe, gui=gui)
    build = delivery.build_delivery

    def damaged(*args, **kwargs):
        result = build(*args, **kwargs)
        (result / "gui/index.html").write_text("damaged")
        return result

    monkeypatch.setattr(delivery, "build_delivery", damaged)
    with pytest.raises(ValueError, match="交付文件缺失、被修改"):
        delivery.build_managed_delivery(home, recipe=recipe, gui=gui)
    assert resolve_delivery(home) == first
