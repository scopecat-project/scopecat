"""Discovery and application loading for a user-owned Scopecat lab project."""

from __future__ import annotations

import sys
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from importlib import import_module
from importlib.metadata import PackageNotFoundError
from importlib.util import find_spec
from pathlib import Path, PureWindowsPath
from threading import RLock
from typing import TYPE_CHECKING, cast

from scopecat.application.capabilities import LabCapabilities
from scopecat.runtime_binding import RuntimeBinding, load_runtime_binding

if TYPE_CHECKING:
    from scopecat.api.lab import LabClient
    from scopecat.application.author_project import AuthorProject
    from scopecat.application.bootstrap import LabBootstrap
    from scopecat.application.lab import LabApplication
    from scopecat.planning.system import ExperimentSystemBuilder
    from scopecat.records.author_revision import AuthorRevisionRef
    from scopecat.sdk.instruments import InstrumentBackend

type LabBootstrapFactory = Callable[[Path], LabBootstrap]
type LabApplicationFactory = Callable[[Path], LabApplication]

_MANIFEST_NAME = "scopecat.toml"
_LAB_KEYS = frozenset(
    {"bootstrap", "application", "instrument_backend", "capabilities", "adapter"}
)


class ProjectManifestError(ValueError):
    """A discovered project manifest cannot define its lab composition."""


class ProjectCodeLoadError(RuntimeError):
    """Project code conflicts with this process's loaded project."""


_project_import_lock = RLock()
_loaded_project_code_root: Path | None = None


@dataclass(frozen=True, slots=True)
class Project:
    """One code project paired with its default daemon-owned instance."""

    root: Path
    manifest: Path
    bootstrap_spec: str | None
    application_spec: str | None
    instrument_backend_spec: str | None
    capabilities: LabCapabilities | None = None
    code_root: Path | None = None
    code_revision: AuthorRevisionRef | None = None
    source_roots: tuple[str, ...] = ()
    refresh_roots: tuple[str, ...] = ()
    installed_packages: tuple[tuple[str, str], ...] = ()
    dependencies: tuple[str, ...] | None = None
    adapter_packages: tuple[tuple[str, str], ...] = ()

    @property
    def runtime_binding(self) -> RuntimeBinding:
        """Local deployment locations; never resolved from captured code."""
        return load_runtime_binding(self.root)

    def load_bootstrap(self) -> LabBootstrap:
        """Load the lightweight composition used by the daemon and config CLI."""

        if self.bootstrap_spec is None:
            from scopecat.application.bootstrap import LabBootstrap

            return LabBootstrap()
        return load_bootstrap_factory(
            self.bootstrap_spec,
            self.code_root or self.root,
            installed_packages=self.adapter_packages,
        )(self.root)

    def load_application(self) -> LabApplication:
        """Load the version-controlled composition declared by this project."""

        if self.application_spec is None and self.capabilities is None:
            from scopecat.application.lab import LabApplication

            return LabApplication()
        from scopecat.author_workspaces import author_workspace_id
        from scopecat.project_sources import loading_revision, loading_workspace

        workspace = author_workspace_id(self.root)
        token = loading_revision.set(self.code_revision)
        workspace_token = loading_workspace.set(workspace)
        try:
            if self.capabilities is not None:
                from scopecat.application.composition import compose_application

                return compose_application(
                    self.capabilities,
                    lambda spec: load_project_symbol(
                        spec,
                        self.code_root or self.root,
                        subject="lab capability",
                        installed_packages=self.adapter_packages,
                    ),
                    self._load_author_module,
                )
            assert self.application_spec is not None
            return load_application_factory(
                self.application_spec, self.code_root or self.root
            )(self.root)
        finally:
            loading_workspace.reset(workspace_token)
            loading_revision.reset(token)

    def _load_author_module(self, name: str) -> object:
        return load_project_symbol(
            name,
            self.code_root or self.root,
            subject="author module",
            installed_packages=self.installed_packages,
        )

    def authoring(self, daemon: str | None = None) -> AuthorProject:
        """Use complete author revisions from notebooks without module reload."""
        from scopecat.application.author_project import AuthorProject
        from scopecat.daemon.endpoint import resolve_daemon_endpoint

        return AuthorProject(
            resolve_daemon_endpoint(self.root, explicit=daemon),
            receipts=self.runtime_binding.data_root / "author-jobs",
            project_root=self.root,
            source_project=self,
            timeout=120,
        )

    def connect(
        self,
        daemon: str | None = None,
        *,
        build_experiment_system: ExperimentSystemBuilder | None = None,
        operator: str = "operator",
    ) -> LabClient:
        """Open the project's high-level notebook client."""

        from scopecat.daemon.endpoint import resolve_daemon_endpoint

        endpoint = resolve_daemon_endpoint(self.root, explicit=daemon)
        application = self.load_application()
        if build_experiment_system is not None:
            application = replace(
                application,
                build_experiment_system=build_experiment_system,
            )
        return application.connect(endpoint, operator=operator)


def open_project(start: str | Path = ".", *, resolve_adapter: bool = True) -> Project:
    """Find ``scopecat.toml`` at or above ``start`` and load its lab settings."""

    selected = Path(start).resolve()
    if selected.is_file():
        if selected.name != _MANIFEST_NAME:
            raise ProjectManifestError(
                f"project manifest must be named {_MANIFEST_NAME}"
            )
        return load_project(selected, resolve_adapter=resolve_adapter)

    for root in (selected, *selected.parents):
        manifest = root / _MANIFEST_NAME
        if manifest.is_file():
            return load_project(manifest, resolve_adapter=resolve_adapter)
    raise ProjectManifestError(f"no {_MANIFEST_NAME} found at or above {selected}")


def load_project(manifest: str | Path, *, resolve_adapter: bool = True) -> Project:
    """Load the project contract shared by daemon and notebook tooling."""

    selected = Path(manifest).resolve()
    try:
        document = cast(
            "dict[str, object]",
            tomllib.loads(selected.read_text(encoding="utf-8")),
        )
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ProjectManifestError(
            f"cannot read project manifest {selected}: {error}"
        ) from error

    lab_value = document.get("lab")
    if not isinstance(lab_value, dict):
        raise ProjectManifestError("scopecat.toml requires a [lab] table")
    lab = cast("dict[str, object]", lab_value)
    unknown = set(lab) - _LAB_KEYS
    if unknown:
        fields = ", ".join(sorted(unknown))
        raise ProjectManifestError(f"unknown [lab] field(s): {fields}")

    lab, adapter_packages = _expand_adapter(lab, resolve_adapter=resolve_adapter)

    bootstrap = _optional_text(lab, "bootstrap")
    application = _optional_text(lab, "application")
    instrument_backend = _optional_text(lab, "instrument_backend")
    capabilities = (
        _parse_capabilities(lab["capabilities"]) if "capabilities" in lab else None
    )
    if application is not None and capabilities is not None:
        raise ProjectManifestError(
            "lab.application and lab.capabilities are mutually exclusive"
        )
    authors = document.get("authors", {})
    if not isinstance(authors, dict):
        raise ProjectManifestError("[authors] must be a table")
    authors = cast("dict[str, object]", authors)
    if set(authors) - {
        "source_roots",
        "refresh_roots",
        "packages",
        "dependencies",
    }:
        raise ProjectManifestError(
            "[authors] accepts source_roots, refresh_roots, packages and dependencies"
        )
    dependencies_value = authors.get("dependencies")
    dependencies: tuple[str, ...] | None = None
    if "dependencies" in authors:
        if not isinstance(dependencies_value, list) or not all(
            isinstance(item, str) and item.strip()
            for item in cast("list[object]", dependencies_value)
        ):
            raise ProjectManifestError(
                "authors.dependencies must be a list of requirements"
            )
        dependencies = tuple(cast("list[str]", dependencies_value))
    packages = authors.get("packages", {})
    if not isinstance(packages, dict):
        raise ProjectManifestError(
            "[authors.packages] must map modules to distributions"
        )
    installed: list[tuple[str, str]] = []
    for module, distribution in cast("dict[str, object]", packages).items():
        if (
            not module.isidentifier()
            or not isinstance(distribution, str)
            or not distribution.strip()
        ):
            raise ProjectManifestError(
                "authors.packages requires top-level module names "
                "and distribution names"
            )
        installed.append((module, distribution))
    merged_packages = dict(adapter_packages)
    for module, owner in installed:
        if module in merged_packages and merged_packages[module] != owner:
            raise ProjectManifestError(
                f"conflicting distribution for module {module!r}"
            )
        merged_packages[module] = owner
    source_roots = _local_roots(authors.get("source_roots", []))
    refresh_roots = _local_roots(authors.get("refresh_roots", []))
    if bool(source_roots) != bool(refresh_roots):
        raise ProjectManifestError(
            "authors requires both source_roots and refresh_roots"
        )
    if any(
        not any(Path(item).is_relative_to(root) for root in source_roots)
        for item in refresh_roots
    ):
        raise ProjectManifestError("refresh_roots must be within source_roots")
    return Project(
        root=selected.parent,
        manifest=selected,
        bootstrap_spec=bootstrap,
        application_spec=application,
        instrument_backend_spec=instrument_backend,
        capabilities=capabilities,
        source_roots=source_roots,
        refresh_roots=refresh_roots,
        installed_packages=tuple(sorted(merged_packages.items())),
        adapter_packages=adapter_packages,
        dependencies=dependencies,
    )


def _expand_adapter(
    lab: dict[str, object], *, resolve_adapter: bool
) -> tuple[dict[str, object], tuple[tuple[str, str], ...]]:
    adapter_packages: tuple[tuple[str, str], ...] = ()
    if "adapter" in lab:
        from scopecat.installed_adapter import (
            load_installed_adapter,
            parse_adapter_reference,
        )

        try:
            reference = parse_adapter_reference(lab["adapter"])
            local_capabilities = lab.get("capabilities", {})
            if (
                set(lab) - {"adapter", "capabilities"}
                or not isinstance(local_capabilities, dict)
                or set(cast("dict[str, object]", local_capabilities))
                - {"author_modules"}
            ):
                raise ValueError(
                    "adapter projects may only add lab.capabilities.author_modules"
                )
            additions = _parse_capabilities(
                cast("dict[str, object]", local_capabilities)
            ).author_modules
            if resolve_adapter:
                adapter = load_installed_adapter(reference)
                adapter_packages = adapter.packages
                lab = adapter.lab.copy()
                declaration = _parse_capabilities(lab.get("capabilities", {}))
                for field in ("bootstrap", "instrument_backend"):
                    spec = _optional_text(lab, field)
                    if spec is not None:
                        _require_adapter_spec(spec, adapter_packages)
                for spec in (
                    *declaration.author_modules,
                    *declaration.procedures,
                    *declaration.procedure_schedules,
                    declaration.experiment_system,
                    declaration.calibrations,
                    declaration.calibration_publications,
                    declaration.launch_provider,
                    declaration.comparison_provider,
                ):
                    if spec is not None:
                        _require_adapter_spec(spec, adapter_packages)
                merged = cast("dict[str, object]", lab.get("capabilities", {})).copy()
                merged["author_modules"] = list(
                    dict.fromkeys((*declaration.author_modules, *additions))
                )
                lab["capabilities"] = merged
            else:
                lab = cast(
                    "dict[str, object]",
                    {"capabilities": {"author_modules": list(additions)}},
                )
        except (OSError, ValueError, PackageNotFoundError) as error:
            raise ProjectManifestError(f"invalid lab adapter: {error}") from error

    return lab, adapter_packages


def load_bootstrap_factory(
    spec: str,
    project_root: str | Path,
    *,
    installed_packages: tuple[tuple[str, str], ...] = (),
) -> LabBootstrapFactory:
    """Load the project factory for daemon and config bootstrap inputs."""

    return cast(
        "LabBootstrapFactory",
        _load_project_factory(
            spec,
            project_root,
            installed_packages=installed_packages,
            subject="lab bootstrap",
        ),
    )


def load_application_factory(
    spec: str,
    project_root: str | Path,
) -> LabApplicationFactory:
    """Load ``MODULE:CALLABLE`` and bind this process to its project root."""

    return cast(
        "LabApplicationFactory",
        _load_project_factory(
            spec,
            project_root,
            subject="lab application",
        ),
    )


def load_instrument_backend_factory(
    spec: str,
    project_root: str | Path,
    *,
    installed_packages: tuple[tuple[str, str], ...] = (),
) -> Callable[[Path], InstrumentBackend]:
    """Load a project-owned backend factory for the instrument worker."""

    return cast(
        "Callable[[Path], InstrumentBackend]",
        _load_project_factory(
            spec,
            project_root,
            installed_packages=installed_packages,
            subject="instrument backend",
        ),
    )


def _load_project_factory(
    spec: str,
    project_root: str | Path,
    *,
    subject: str,
    installed_packages: tuple[tuple[str, str], ...] = (),
) -> Callable[[Path], object]:
    module, separator, attribute = spec.partition(":")
    if not module or not separator or not attribute:
        raise ValueError(f"{subject} must use MODULE:CALLABLE")
    factory = load_project_symbol(
        spec,
        project_root,
        subject=subject,
        require_callable=True,
        installed_packages=installed_packages,
    )
    return cast("Callable[[Path], object]", factory)


def load_project_symbol(
    spec: str,
    project_root: str | Path,
    *,
    subject: str,
    require_callable: bool = False,
    installed_packages: tuple[tuple[str, str], ...] = (),
) -> object:
    module_name, separator, attribute_name = spec.partition(":")
    if not module_name or (separator and not attribute_name):
        raise ValueError(f"{subject} must use MODULE or MODULE:ATTRIBUTE")

    root = Path(project_root).resolve()
    owner = dict(installed_packages).get(module_name.partition(".")[0])
    if owner is not None:
        return _load_installed_symbol(
            spec, root, owner, require_callable=require_callable
        )
    with _project_import_lock:
        _require_available_project(root)
        _require_unshadowed_module(module_name, root, subject=subject)
        before = frozenset(sys.modules)
        inserted_paths = _add_project_import_paths(root)
        try:
            try:
                module = import_module(module_name)
            except ModuleNotFoundError as error:
                raise ProjectCodeLoadError(
                    f"cannot load project {subject} {spec!r}: missing Python "
                    f"module {error.name!r}. Install the project's application "
                    "dependencies in the Python environment running Scopecat."
                ) from error
            if not _module_belongs_to_project(module, root):
                raise ProjectCodeLoadError(
                    f"project {subject} module {module_name!r} resolved outside "
                    f"project {root}: {_module_locations_text(module)}"
                )
            value = (
                cast("object", getattr(module, attribute_name)) if separator else module
            )
            if require_callable and not callable(value):
                raise ProjectCodeLoadError(
                    f"project {subject} {spec!r} does not name a callable"
                )
        except BaseException:
            _remove_new_project_modules(root, module_name, before)
            _remove_import_paths(inserted_paths)
            raise

        global _loaded_project_code_root
        _loaded_project_code_root = root

    return value


def _require_adapter_spec(spec: str, packages: tuple[tuple[str, str], ...]) -> None:
    from scopecat.installed_authors import installed_module_path

    module = spec.partition(":")[0]
    owner = dict(packages).get(module.partition(".")[0])
    if owner is None:
        raise ValueError(f"adapter symbol {spec!r} has no declared package owner")
    installed_module_path(module.partition(".")[0], owner, module)


def _load_installed_symbol(
    spec: str, root: Path, owner: str, *, require_callable: bool
) -> object:
    from scopecat.installed_authors import installed_module_path

    module_name, separator, attribute = spec.partition(":")
    with _project_import_lock:
        _require_available_project(root)
        parts = module_name.split(".")
        for index in range(1, len(parts) + 1):
            name = ".".join(parts[:index])
            expected = installed_module_path(parts[0], owner, name)
            loaded = sys.modules.get(name)
            origin = loaded.__file__ if loaded is not None else None
            if loaded is None:
                found = find_spec(name)
                origin = None if found is None else found.origin
            if origin is None or Path(origin).resolve() != expected.resolve():
                raise ProjectCodeLoadError(
                    f"installed module {name!r} did not resolve to the declared "
                    f"distribution {owner!r}: expected {expected}"
                )
            import_module(name)
        module = import_module(module_name)
        value = cast("object", getattr(module, attribute)) if separator else module
        if require_callable and not callable(value):
            raise ProjectCodeLoadError(f"installed symbol {spec!r} is not callable")
        global _loaded_project_code_root
        _loaded_project_code_root = root
        return value


def _require_available_project(root: Path) -> None:
    loaded = _loaded_project_code_root
    if loaded is None or loaded == root:
        return
    raise ProjectCodeLoadError(
        f"this process already loaded project code from {loaded}; "
        f"cannot also load {root}. Run each Scopecat project in a separate process."
    )


def _require_unshadowed_module(
    module_name: str,
    root: Path,
    *,
    subject: str,
) -> None:
    parts = module_name.split(".")
    for index in range(1, len(parts) + 1):
        loaded_name = ".".join(parts[:index])
        loaded = sys.modules.get(loaded_name)
        if loaded is not None and not _module_belongs_to_project(loaded, root):
            raise ProjectCodeLoadError(
                f"cannot load project {subject} {module_name!r} from {root}: "
                f"module {loaded_name!r} is already loaded from outside this "
                f"project ({_module_locations_text(loaded)})"
            )


def _module_belongs_to_project(module: object, root: Path) -> bool:
    locations = _module_locations(module)
    return bool(locations) and all(
        location.is_relative_to(root) for location in locations
    )


def _module_locations(module: object) -> tuple[Path, ...]:
    selected: list[Path] = []
    filename = cast("object", getattr(module, "__file__", None))
    if isinstance(filename, str):
        selected.append(Path(filename).resolve())
    module_path = cast("object", getattr(module, "__path__", None))
    if isinstance(module_path, Iterable):
        selected.extend(
            Path(path).resolve() for path in module_path if isinstance(path, str)
        )
    return tuple(dict.fromkeys(selected))


def _module_locations_text(module: object) -> str:
    locations = _module_locations(module)
    return ", ".join(str(path) for path in locations) if locations else "unknown origin"


def _remove_new_project_modules(
    root: Path,
    module_name: str,
    before: frozenset[str],
) -> None:
    root_name = module_name.partition(".")[0]
    for loaded_name, loaded in tuple(sys.modules.items()):
        if loaded_name in before:
            continue
        if (
            loaded_name == root_name
            or loaded_name.startswith(f"{root_name}.")
            or _module_belongs_to_project(loaded, root)
        ):
            sys.modules.pop(loaded_name, None)


def _add_project_import_paths(root: Path) -> tuple[str, ...]:
    """Expose the bound project's imports for the remaining process lifetime."""

    inserted: list[str] = []
    for path in (root, root / "src"):
        selected = str(path)
        if path.is_dir() and selected not in sys.path:
            sys.path.insert(0, selected)
            inserted.append(selected)
    return tuple(inserted)


def _remove_import_paths(paths: Iterable[str]) -> None:
    for selected in paths:
        if selected in sys.path:
            sys.path.remove(selected)


def _local_roots(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ProjectManifestError("author roots must be lists of local paths")
    selected: list[str] = []
    for item in cast("list[object]", value):
        if (
            not isinstance(item, str)
            or not item
            or PureWindowsPath(item).drive
            or Path(item).is_absolute()
            or ".." in Path(item).parts
            or "\\" in item
            or item == "."
        ):
            raise ProjectManifestError(
                "author roots must be nonempty relative subdirectories"
            )
        selected.append(item)
    return tuple(selected)


def _parse_capabilities(value: object) -> LabCapabilities:
    if not isinstance(value, dict):
        raise ProjectManifestError("[lab.capabilities] must be a table")
    table = cast("dict[str, object]", value)
    sequence_fields = {"author_modules", "procedures", "procedure_schedules"}
    object_fields = {
        "experiment_system",
        "calibrations",
        "calibration_publications",
        "launch_provider",
        "comparison_provider",
    }
    unknown = set(table) - sequence_fields - object_fields
    if unknown:
        raise ProjectManifestError(
            f"unknown [lab.capabilities] field(s): {', '.join(sorted(unknown))}"
        )
    sequences: dict[str, tuple[str, ...]] = {}
    for name in sequence_fields:
        items = table.get(name, [])
        if not isinstance(items, list) or not all(
            isinstance(item, str) and item.strip()
            for item in cast("list[object]", items)
        ):
            raise ProjectManifestError(
                f"lab.capabilities.{name} must be a list of import names"
            )
        sequences[name] = tuple(cast("list[str]", items))
    objects = {name: _optional_text(table, name) for name in object_fields}
    for name, specs in (
        *((name, values) for name, values in sequences.items()),
        *((name, (spec,)) for name, spec in objects.items() if spec is not None),
    ):
        for spec in specs:
            module, separator, attribute = spec.partition(":")
            if (
                not all(part.isidentifier() for part in module.split("."))
                or (
                    name != "author_modules"
                    and (not separator or not attribute.isidentifier())
                )
                or (name == "author_modules" and separator)
            ):
                raise ProjectManifestError(
                    f"invalid lab.capabilities.{name} import name: {spec!r}"
                )
    return LabCapabilities(
        author_modules=sequences["author_modules"],
        procedures=sequences["procedures"],
        procedure_schedules=sequences["procedure_schedules"],
        experiment_system=objects["experiment_system"],
        calibrations=objects["calibrations"],
        calibration_publications=objects["calibration_publications"],
        launch_provider=objects["launch_provider"],
        comparison_provider=objects["comparison_provider"],
    )


def _optional_text(table: dict[str, object], field: str) -> str | None:
    value = table.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProjectManifestError(f"[lab].{field} must be a non-empty string")
    return value


__all__ = [
    "LabApplicationFactory",
    "LabBootstrapFactory",
    "Project",
    "ProjectCodeLoadError",
    "ProjectManifestError",
    "load_application_factory",
    "load_bootstrap_factory",
    "load_instrument_backend_factory",
    "load_project",
    "open_project",
]
