"""Lightweight daemon bootstrap composition for one lab project."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from scopecat.records.configuration_template import ConfigurationTemplate
from scopecat.records.parameter_revision import ParameterRevisionContent
from scopecat.records.setup import ExecutableSetupSnapshot


@dataclass(frozen=True, slots=True)
class LabBootstrap:
    """Configuration inputs required before the daemon can serve a project.

    This composition deliberately excludes notebook and worker execution
    callbacks. A daemon can therefore initialize durable project state without
    importing procedures, schedules, calibrations, or publication policies from
    the project's full application.

    ``setup`` seeds equipment only on first use. ``parameter_defaults`` optionally
    seeds the transitional execution default; it is not an author parameter
    branch or a routine save hook. Both factories remain unevaluated on an
    initialized restart. Authors edit independent parameter branches instead.
    """

    setup: Callable[[], ExecutableSetupSnapshot] | None = None
    parameter_defaults: Callable[[], ParameterRevisionContent] | None = None
    configuration_templates: Callable[[], tuple[ConfigurationTemplate, ...]] | None = (
        None
    )


__all__ = ["LabBootstrap"]
