"""Lightweight daemon bootstrap composition for one lab project."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.configuration_template import ConfigurationTemplate

type BootstrapConfigFactory = Callable[[], ConfigProfileSnapshot]


@dataclass(frozen=True, slots=True)
class LabBootstrap:
    """Configuration inputs required before the daemon can serve a project.

    This composition deliberately excludes notebook and worker execution
    callbacks. A daemon can therefore initialize durable project state without
    importing procedures, schedules, calibrations, or publication policies from
    the project's full application.
    """

    bootstrap_config: BootstrapConfigFactory | None = None
    configuration_templates: Callable[[], tuple[ConfigurationTemplate, ...]] | None = (
        None
    )


__all__ = ["BootstrapConfigFactory", "LabBootstrap"]
