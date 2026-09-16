"""Explicit replacement of local author modules from an admitted source bundle.

Python aliases are deliberately not patched. The retained server revision, rather
than a notebook's mutable import table, owns execution and historical analysis.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
from collections.abc import Sequence
from dataclasses import replace
from importlib.machinery import ModuleSpec
from pathlib import Path
from threading import RLock
from types import ModuleType
from typing import cast, override

from scopecat.application.authoring import AuthorExperiment
from scopecat.authoring.experiments import Experiment
from scopecat.project_sources import materialize_sources, require_environment
from scopecat.records.author_revision import AuthorRevisionBundle

_import_lock = RLock()


class _RevisionImports(importlib.abc.MetaPathFinder):
    def __init__(self, project_root: Path, archive: Path, roots: tuple[str, ...]):
        self.project_root = project_root
        self.archive = archive
        self.roots = roots
        # These are the same two import locations supported by Project loading.
        self.locations = (Path("src"), Path())
        prefixes: list[str] = []
        for name in roots:
            root = Path(name)
            relative = root.relative_to("src") if root.is_relative_to("src") else root
            if relative.parts:
                prefixes.append(".".join(relative.parts))
            else:
                prefixes.extend(
                    path.stem
                    for path in (archive / root).iterdir()
                    if path.name != "__pycache__"
                    and (path.is_dir() or path.suffix == ".py")
                )
        self.prefixes = tuple(prefixes)

    def paths(self, name: str) -> tuple[Path, ...]:
        return tuple(base / name.replace(".", "/") for base in self.locations)

    def owns(self, name: str) -> bool:
        return any(
            name == prefix or name.startswith(prefix + ".") for prefix in self.prefixes
        )

    @override
    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        if not self.owns(fullname):
            return None
        for relative in self.paths(fullname):
            if not any(relative.is_relative_to(root) for root in self.roots):
                continue
            location = self.archive / relative
            if location.with_suffix(".py").is_file():
                return importlib.util.spec_from_file_location(
                    fullname, location.with_suffix(".py")
                )
            if (location / "__init__.py").is_file():
                return importlib.util.spec_from_file_location(
                    fullname, location / "__init__.py"
                )
            if location.is_dir():
                spec = ModuleSpec(fullname, loader=None, is_package=True)
                spec.submodule_search_locations = [str(location)]
                return spec
        # A deleted helper must not fall through to mutable workspace source.
        raise ModuleNotFoundError(
            f"{fullname} is absent from the selected author revision"
        )


def load_revision_experiment[**P, ResultT](
    experiment: Experiment[P, ResultT],
    bundle: AuthorRevisionBundle,
    *,
    project_root: Path,
    cache: Path,
    expected_fingerprint: str,
) -> Experiment[P, ResultT]:
    """Replace the refresh closure transactionally, then validate the declaration."""
    module_name = experiment.source["module"]
    qualname = experiment.source["qualname"]
    archive = materialize_sources(bundle, cache)
    finder = _RevisionImports(project_root, archive, bundle.manifest.refresh_roots)
    if not finder.owns(module_name) or "<locals>" in qualname:
        raise ValueError(
            "typed refresh requires an importable experiment inside refresh_roots"
        )
    require_environment(bundle.manifest)
    with _import_lock:
        previous_finders = [
            item
            for item in sys.meta_path
            if isinstance(item, _RevisionImports) and item.project_root == project_root
        ]
        finder.prefixes = tuple(
            dict.fromkeys(
                (
                    *finder.prefixes,
                    *(prefix for item in previous_finders for prefix in item.prefixes),
                )
            )
        )
        previous = {
            name: module
            for name, module in sys.modules.copy().items()
            if finder.owns(name)
        }
        allowed = (project_root, *(item.archive for item in previous_finders))
        for name, module in previous.items():
            filename = getattr(module, "__file__", None)
            locations = [filename] if filename is not None else list(module.__path__)
            if not locations or not all(
                any(Path(location).resolve().is_relative_to(root) for root in allowed)
                for location in locations
            ):
                raise ValueError(
                    f"author module {name} is shadowed by another project "
                    "or installed package"
                )
        # Only child-module attributes can be changed by import machinery. Preserve
        # those bindings on retained parent packages if loading fails.
        parents: dict[str, tuple[ModuleType, str, object]] = {}
        missing = object()
        for name in previous:
            parent_name, _, child = name.rpartition(".")
            parent = sys.modules.get(parent_name)
            if parent is not None:
                parents[name] = (parent, child, getattr(parent, child, missing))
        for name in previous:
            del sys.modules[name]
            if name in parents and name.rpartition(".")[0] not in previous:
                parent, child, _ = parents[name]
                if hasattr(parent, child):
                    delattr(parent, child)
        for item in previous_finders:
            sys.meta_path.remove(item)
        sys.meta_path.insert(0, finder)
        try:
            value: object = importlib.import_module(module_name)
            for part in qualname.split("."):
                value = cast("object", getattr(value, part))
            if not isinstance(value, Experiment) or value.id != experiment.id:
                raise ValueError(
                    f"{module_name}:{qualname} no longer declares {experiment.id}"
                )
            selected = cast("Experiment[P, ResultT]", value)
            declaration = AuthorExperiment.from_declaration(
                selected, code_revision=bundle.manifest.ref
            )
            if declaration.fingerprint != expected_fingerprint:
                raise ValueError(
                    "notebook declaration does not match the admitted source revision"
                )
            return replace(selected, code_revision=bundle.manifest.ref)
        except BaseException:
            for name in tuple(sys.modules):
                if finder.owns(name):
                    del sys.modules[name]
                    parent_name, _, child = name.rpartition(".")
                    parent = sys.modules.get(parent_name)
                    if parent is not None and hasattr(parent, child):
                        delattr(parent, child)
            sys.modules.update(previous)
            for parent, child, value in parents.values():
                if value is missing:
                    if hasattr(parent, child):
                        delattr(parent, child)
                else:
                    setattr(parent, child, value)
            sys.meta_path.remove(finder)
            sys.meta_path[:0] = previous_finders
            raise
