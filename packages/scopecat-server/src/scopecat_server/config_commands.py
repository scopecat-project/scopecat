"""Project configuration operations shared by the command-line interface."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path
from uuid import uuid4

from scopecat.config.documents import config_snapshot_document_json
from scopecat.config.resolution import (
    compose_configuration,
    config_revision_entry_id,
)
from scopecat.daemon.client import DaemonNotFoundError
from scopecat.daemon.endpoint import (
    DaemonEndpointError,
    read_daemon_endpoint_record,
)
from scopecat.daemon.wire import ConfigActivationReceipt, ConfigPublishReceipt
from scopecat.project import Project
from scopecat.records.config import (
    ConfigContentHash,
    ConfigProfileSnapshot,
    config_content_hash,
)


@dataclass(frozen=True, slots=True)
class ProjectConfigDiff:
    """Comparison between executable project source and the daemon default."""

    source: ConfigProfileSnapshot
    active: ConfigProfileSnapshot
    source_content_hash: ConfigContentHash
    active_content_hash: ConfigContentHash

    @property
    def has_drift(self) -> bool:
        return self.source_content_hash != self.active_content_hash

    def unified_json_diff(self) -> tuple[str, ...]:
        """Render the active-to-source change without defining config semantics."""

        if not self.has_drift:
            return ()
        return tuple(
            unified_diff(
                _snapshot_json(self.active).splitlines(),
                _snapshot_json(self.source).splitlines(),
                fromfile="daemon-default",
                tofile="project-source",
                lineterm="",
            )
        )


@dataclass(frozen=True, slots=True)
class ProjectConfigApplyResult:
    """Result of publishing freshly evaluated project source as the default."""

    source: ConfigProfileSnapshot
    previous: ConfigProfileSnapshot
    entry_id: str
    receipt: ConfigPublishReceipt | ConfigActivationReceipt | None
    source_content_hash: ConfigContentHash
    previous_content_hash: ConfigContentHash

    @property
    def changed(self) -> bool:
        return self.source_content_hash != self.previous_content_hash


@dataclass(frozen=True, slots=True)
class ProjectConfigExportResult:
    """A complete daemon-default snapshot written to an explicit file."""

    destination: Path
    config: ConfigProfileSnapshot
    content_hash: ConfigContentHash


def load_source_config(project: Project) -> ConfigProfileSnapshot:
    """Freshly evaluate and validate the project's executable config source."""

    bootstrap = project.load_bootstrap()
    if bootstrap.setup is None or bootstrap.parameter_defaults is None:
        raise ValueError("project bootstrap must define setup and parameter_defaults")
    setup = bootstrap.setup()
    parameters = bootstrap.parameter_defaults()
    return compose_configuration(
        setup,
        id=parameters.id,
        system_id=parameters.system_id,
        catalog=parameters.catalog,
        parameters=parameters.parameters,
    )


def diff_project_config(project: Project) -> ProjectConfigDiff:
    """Compare freshly evaluated project source with the daemon default."""

    source = load_source_config(project)
    with project.connect(_recorded_daemon_url(project)) as lab:
        active = lab.resolve_config("active")
    return ProjectConfigDiff(
        source=source,
        active=active,
        source_content_hash=config_content_hash(source),
        active_content_hash=config_content_hash(active),
    )


def apply_project_config(
    project: Project,
    *,
    actor: str = "operator",
    note: str = "apply project config source",
) -> ProjectConfigApplyResult:
    """Publish freshly evaluated source through the ordinary config intent API."""

    source = load_source_config(project)
    source_content_hash = config_content_hash(source)
    with project.connect(_recorded_daemon_url(project)) as lab:
        active = lab.config.active()
        previous = active.config
        previous_content_hash = config_content_hash(previous)
        if source_content_hash == previous_content_hash:
            entry_id = active.entry.id
            receipt = None
        else:
            entry_id = config_revision_entry_id(source)
            try:
                existing = lab.config.entry(entry_id)
            except DaemonNotFoundError:
                receipt = lab.config.set_default(
                    source,
                    actor=actor,
                    note=note,
                )
            else:
                if config_content_hash(existing.config) != source_content_hash:
                    raise RuntimeError(
                        f"config registry entry {entry_id!r} has unexpected content"
                    )
                receipt = lab.config.activate_entry(
                    entry_id,
                    operation_id=f"config-apply-{uuid4().hex}",
                    expected_generation=active.activation.generation,
                    actor=actor,
                    note=note,
                )
    return ProjectConfigApplyResult(
        source=source,
        previous=previous,
        entry_id=entry_id,
        receipt=receipt,
        source_content_hash=source_content_hash,
        previous_content_hash=previous_content_hash,
    )


def export_project_config(
    project: Project,
    destination: str | Path,
    *,
    overwrite: bool = False,
) -> ProjectConfigExportResult:
    """Atomically export the daemon default as one complete JSON snapshot."""

    selected = Path(destination).resolve()
    if selected.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing file: {selected}")

    with project.connect(_recorded_daemon_url(project)) as lab:
        active = lab.resolve_config("active")
    _write_snapshot(selected, active, overwrite=overwrite)
    return ProjectConfigExportResult(
        destination=selected,
        config=active,
        content_hash=config_content_hash(active),
    )


def _snapshot_json(config: ConfigProfileSnapshot) -> str:
    return f"{config_snapshot_document_json(config, indent=2)}\n"


def _recorded_daemon_url(project: Project) -> str:
    """Resolve only the daemon record owned by the selected project."""

    record = read_daemon_endpoint_record(project.root)
    if record is None:
        raise DaemonEndpointError(
            f"no daemon endpoint for {project.root}; start it with 'scopecat start'"
        )
    if record.project_root.resolve() != project.root:
        raise DaemonEndpointError(
            f"daemon record belongs to another project: {record.project_root}"
        )
    return record.base_url


def _write_snapshot(
    destination: Path,
    config: ConfigProfileSnapshot,
    *,
    overwrite: bool,
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_snapshot_json(config))
            stream.flush()
            os.fsync(stream.fileno())
        if destination.exists() and not overwrite:
            raise FileExistsError(f"refusing to overwrite existing file: {destination}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "ProjectConfigApplyResult",
    "ProjectConfigDiff",
    "ProjectConfigExportResult",
    "apply_project_config",
    "diff_project_config",
    "export_project_config",
    "load_source_config",
]
