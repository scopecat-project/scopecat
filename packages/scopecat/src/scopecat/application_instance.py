"""Application identity and local storage, independent of author source manifests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from scopecat.project import Project, open_project
from scopecat.runtime_binding import (
    APPLICATION_INSTANCE_NAME,
    RuntimeBinding,
    application_binding,
    read_application_layout,
)


@dataclass(frozen=True, slots=True)
class ApplicationInstance:
    """One local data/device owner. No author code is needed to open it."""

    root: Path
    layout: Literal["application", "legacy"] = "application"

    @property
    def runtime_binding(self) -> RuntimeBinding:
        return application_binding(self.root, self.layout)

    def declaration(self) -> str:
        return json.dumps({"format": 1, "layout": self.layout}, indent=2) + "\n"

    @classmethod
    def open(cls, root: str | Path) -> ApplicationInstance:
        root = Path(root).resolve()
        return cls(root, read_application_layout(root))


def open_instance(
    root: str | Path, *, resolve_adapter: bool = True
) -> Project | ApplicationInstance:
    """Maintenance opens an exact instance or explicit development composition."""
    selected = Path(root).resolve()
    if (selected / APPLICATION_INSTANCE_NAME).exists():
        return ApplicationInstance.open(selected)
    return open_project(selected, resolve_adapter=resolve_adapter)
