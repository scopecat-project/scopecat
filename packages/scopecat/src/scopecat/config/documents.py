"""External configuration document formats."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import ConfigDict

from scopecat.records.config import ConfigProfileSnapshot

CONFIG_SNAPSHOT_FORMAT_VERSION = "scopecat.config_snapshot.v11"


class _ConfigSnapshotDocument(ConfigProfileSnapshot):
    model_config = ConfigDict(extra="forbid")

    format_version: Literal["scopecat.config_snapshot.v11"] = (
        CONFIG_SNAPSHOT_FORMAT_VERSION
    )


def parse_config_snapshot_document(content: str) -> ConfigProfileSnapshot:
    """Validate an exported snapshot document and return its runtime value."""

    document = _ConfigSnapshotDocument.model_validate_json(content)
    return ConfigProfileSnapshot.model_validate(
        document.model_dump(mode="python", exclude={"format_version"})
    )


def config_snapshot_document_json(
    config: ConfigProfileSnapshot,
    *,
    indent: int | None = None,
) -> str:
    """Serialize a runtime snapshot as a versioned external document."""

    document = _ConfigSnapshotDocument(
        id=config.id,
        system=config.system,
        parameter_snapshot=config.parameter_snapshot,
    )
    return document.model_dump_json(indent=indent)


def load_config_snapshot_document(path: str | Path) -> ConfigProfileSnapshot:
    """Load one complete exported configuration snapshot."""

    return parse_config_snapshot_document(Path(path).read_text(encoding="utf-8"))


__all__ = [
    "CONFIG_SNAPSHOT_FORMAT_VERSION",
    "config_snapshot_document_json",
    "load_config_snapshot_document",
    "parse_config_snapshot_document",
]
