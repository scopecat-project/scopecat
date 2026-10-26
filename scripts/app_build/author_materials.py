"""Prepare only source desktop author materials and its optional host wheel."""

from __future__ import annotations

import shutil
import sys
import tomllib
from importlib.metadata import version
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

from scripts.app_build import cocoa_dependency, delivery, toolchain, windows_dependency
from scripts.app_build.author_resources import AuthorInputs
from scripts.app_build.preview import download

from scopecat_app.author_resources import PACKAGES
from scopecat_app.bundle import file_hash, inventory, target_identity


def prepare(
    source: Path,
    destination: Path,
    gui: Path,
    *,
    source_digest: str,
    native_wheels: Path,
) -> AuthorInputs:
    """Use the locked source interpreter; never construct an offline runtime."""
    source, destination = source.resolve(), destination.resolve()
    toolchain.baseline(source)
    locked = cast(
        "dict[str, list[dict[str, object]]]",
        tomllib.loads((source / "uv.lock").read_text(encoding="utf-8")),
    )
    if [p["version"] for p in locked["package"] if p["name"] == "uv"] != [
        version("uv")
    ]:
        raise ValueError("Author material preparation requires the locked uv version")
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copytree(gui, destination / "gui")
    wheels = destination / "wheels"
    wheels.mkdir()
    packages = tuple(source / "packages" / name.replace("_", "-") for name in PACKAGES)
    plan = delivery.DeliveryRecipe(source, source, "delivery", False, packages)
    constraints = destination / "requirements.lock"
    delivery.export_dependencies(
        plan, constraints, {name.replace("_", "-") for name in PACKAGES}
    )
    delivery.build_local_wheels(
        packages,
        wheels,
        repository=source,
        python=Path(sys.executable),
        constraints=constraints,
        version=delivery.package_version(source, None, release=False),
    )
    # Keep registry hashes/markers from the reviewed export, then constrain the
    # two source-built distributions to these exact stamped wheel bytes.
    with constraints.open("a", encoding="utf-8") as stream:
        for wheel in sorted(wheels.glob("*.whl")):
            name, package_version = delivery.wheel_metadata(wheel)
            stream.write(
                f"\n{name}=={package_version} --hash=sha256:{file_hash(wheel)}\n"
            )
    if sys.platform in {"darwin", "win32"}:
        prepare_native_wheel(source, native_wheels)
    files = inventory(destination, ("gui",))
    files.update(
        {f"wheels/{wheel.name}": file_hash(wheel) for wheel in wheels.glob("*.whl")}
    )
    files["requirements.lock"] = file_hash(constraints)
    return AuthorInputs(
        destination, target_identity(), {"public": source_digest}, files
    )


def prepare_native_wheel(source: Path, destination: Path) -> None:
    """Fetch just the locked upstream pywebview wheel and apply the reviewed repair."""
    patch = cocoa_dependency if sys.platform == "darwin" else windows_dependency
    locked = cast(
        "dict[str, list[dict[str, object]]]",
        tomllib.loads((source / "uv.lock").read_text(encoding="utf-8")),
    )
    (package,) = [item for item in locked["package"] if item["name"] == "pywebview"]
    if package["version"] != patch.UPSTREAM_VERSION:
        raise ValueError("Host pywebview lock differs from the reviewed repair")
    name = f"pywebview-{patch.UPSTREAM_VERSION}-py3-none-any.whl"
    (wheel,) = [
        item
        for item in cast("list[dict[str, str]]", package["wheels"])
        if Path(urlparse(item["url"]).path).name == name
    ]
    if wheel["hash"] != f"sha256:{patch.UPSTREAM_SHA256}":
        raise ValueError("Host pywebview hash differs from the reviewed repair")
    destination.mkdir(parents=True, exist_ok=False)
    download(wheel["url"], patch.UPSTREAM_SHA256, destination / name)
    patch.patch_wheel(destination)
    patch.verify_wheel(destination)
