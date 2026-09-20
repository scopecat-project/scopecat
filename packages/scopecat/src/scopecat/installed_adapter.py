"""Read a distribution-owned laboratory declaration without importing its code."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib.metadata import distribution
from pathlib import Path, PurePosixPath
from typing import cast

from packaging.utils import canonicalize_name

from scopecat.installed_authors import installed_module_path


@dataclass(frozen=True, slots=True)
class AdapterReference:
    distribution: str
    manifest: str


@dataclass(frozen=True, slots=True)
class InstalledAdapter:
    lab: dict[str, object]
    packages: tuple[tuple[str, str], ...]


def parse_adapter_reference(value: object) -> AdapterReference:
    if not isinstance(value, dict) or set(cast("dict[str, object]", value)) != {
        "distribution",
        "manifest",
    }:
        raise ValueError("[lab.adapter] requires distribution and manifest")
    table = cast("dict[str, object]", value)
    name, resource = table["distribution"], table["manifest"]
    if not isinstance(name, str) or not name.strip():
        raise ValueError(
            "lab.adapter.distribution must be a nonempty distribution name"
        )
    if (
        not isinstance(resource, str)
        or not resource
        or PurePosixPath(resource).is_absolute()
        or any(part in {"..", "."} for part in resource.split("/"))
        or "\\" in resource
        or ":" in resource
        or PurePosixPath(resource).suffix != ".toml"
    ):
        raise ValueError("lab.adapter.manifest must be a relative TOML resource path")
    return AdapterReference(name, resource)


def load_installed_adapter(reference: AdapterReference) -> InstalledAdapter:
    """Read only wheel-owned data and verify every declared module owner."""
    dist = distribution(reference.distribution)
    inventory = {str(item) for item in dist.files or ()}
    if reference.manifest not in inventory:
        raise ValueError("adapter manifest is not owned by the declared distribution")
    path = Path(str(dist.locate_file(reference.manifest)))
    if path.is_symlink():
        raise ValueError("adapter manifest must not be a symlink")
    document = cast(
        "dict[str, object]", tomllib.loads(path.read_text(encoding="utf-8"))
    )
    if set(document) != {"lab", "authors"}:
        raise ValueError("adapter manifest requires only [lab] and [authors.packages]")
    lab = document["lab"]
    if not isinstance(lab, dict) or set(cast("dict[str, object]", lab)) - {
        "bootstrap",
        "instrument_backend",
        "capabilities",
    }:
        raise ValueError(
            "adapter [lab] accepts bootstrap, instrument_backend and capabilities"
        )
    authors = document["authors"]
    if not isinstance(authors, dict) or set(cast("dict[str, object]", authors)) != {
        "packages"
    }:
        raise ValueError("adapter [authors] requires only packages")
    packages = cast("dict[str, object]", authors)["packages"]
    if not isinstance(packages, dict) or not packages:
        raise ValueError("adapter authors.packages must declare owned modules")
    selected: list[tuple[str, str]] = []
    owns_resource = False
    for module, owner in cast("dict[str, object]", packages).items():
        if not module.isidentifier() or not isinstance(owner, str) or not owner.strip():
            raise ValueError("adapter packages requires module names and distributions")
        module_path = installed_module_path(module, owner, module)
        if (
            canonicalize_name(owner) == canonicalize_name(reference.distribution)
            and module_path.name == "__init__.py"
            and path.resolve().is_relative_to(module_path.parent.resolve())
        ):
            owns_resource = True
        selected.append((module, owner))
    if not owns_resource:
        raise ValueError("adapter manifest must be inside its declared owned package")
    return InstalledAdapter(cast("dict[str, object]", lab), tuple(sorted(selected)))
