"""Discovery and application loading for a user-owned Scopecat lab project."""

from __future__ import annotations

import sys
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from importlib import import_module
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, cast

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
_LAB_KEYS = frozenset({"bootstrap", "application", "instrument_backend"})


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
    code_root: Path | None = None
    code_revision: AuthorRevisionRef | None = None
    source_roots: tuple[str, ...] = ()
    refresh_roots: tuple[str, ...] = ()

    def load_bootstrap(self) -> LabBootstrap:
        """Load the lightweight composition used by the daemon and config CLI."""

        if self.bootstrap_spec is None:
            from scopecat.application.bootstrap import LabBootstrap

            return LabBootstrap()
        return load_bootstrap_factory(self.bootstrap_spec, self.code_root or self.root)(
            self.root
        )

    def load_application(self) -> LabApplication:
        """Load the version-controlled composition declared by this project."""

        if self.application_spec is None:
            from scopecat.application.lab import LabApplication

            return LabApplication()
        return load_application_factory(
            self.application_spec, self.code_root or self.root
        )(self.root)

    def authoring(self, daemon: str | None = None) -> AuthorProject:
        """Use complete author revisions from notebooks without module reload."""
        from scopecat.application.author_project import AuthorProject
        from scopecat.daemon.endpoint import resolve_daemon_endpoint

        return AuthorProject(resolve_daemon_endpoint(self.root, explicit=daemon))

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


def open_project(start: str | Path = ".") -> Project:
    """Find ``scopecat.toml`` at or above ``start`` and load its lab settings."""

    selected = Path(start).resolve()
    if selected.is_file():
        if selected.name != _MANIFEST_NAME:
            raise ProjectManifestError(
                f"project manifest must be named {_MANIFEST_NAME}"
            )
        return load_project(selected)

    for root in (selected, *selected.parents):
        manifest = root / _MANIFEST_NAME
        if manifest.is_file():
            return load_project(manifest)
    raise ProjectManifestError(f"no {_MANIFEST_NAME} found at or above {selected}")


def load_project(manifest: str | Path) -> Project:
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

    bootstrap = _optional_text(lab, "bootstrap")
    application = _optional_text(lab, "application")
    instrument_backend = _optional_text(lab, "instrument_backend")
    authors = document.get("authors", {})
    if not isinstance(authors, dict):
        raise ProjectManifestError("[authors] must be a table")
    authors = cast("dict[str, object]", authors)
    if set(authors) - {
        "source_roots",
        "refresh_roots",
    }:
        raise ProjectManifestError("[authors] accepts source_roots and refresh_roots")
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
        source_roots=source_roots,
        refresh_roots=refresh_roots,
    )


def load_bootstrap_factory(
    spec: str,
    project_root: str | Path,
) -> LabBootstrapFactory:
    """Load the project factory for daemon and config bootstrap inputs."""

    return cast(
        "LabBootstrapFactory",
        _load_project_factory(
            spec,
            project_root,
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
) -> Callable[[Path], InstrumentBackend]:
    """Load a project-owned backend factory for the instrument worker."""

    return cast(
        "Callable[[Path], InstrumentBackend]",
        _load_project_factory(
            spec,
            project_root,
            subject="instrument backend",
        ),
    )


def _load_project_factory(
    spec: str,
    project_root: str | Path,
    *,
    subject: str,
) -> Callable[[Path], object]:
    module_name, separator, attribute_name = spec.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError(f"{subject} must use MODULE:CALLABLE")

    root = Path(project_root).resolve()
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
            factory = cast("object", getattr(module, attribute_name))
            if not callable(factory):
                raise ProjectCodeLoadError(
                    f"project {subject} {spec!r} does not name a callable"
                )
        except BaseException:
            _remove_new_project_modules(root, module_name, before)
            _remove_import_paths(inserted_paths)
            raise

        global _loaded_project_code_root
        _loaded_project_code_root = root

    return cast("Callable[[Path], object]", factory)


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
