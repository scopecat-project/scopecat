"""Declarative inputs for the standard public application composition.

Only import names live here. Loading a manifest or bootstrapping a daemon never
imports experiment, calibration or analysis implementations.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LabCapabilities:
    """Import declarations resolved by public code in a qualified worker.

    Objects use ``module:attribute`` names; providers and registries are values,
    not factories. Experiment system builders retain their normal callable API.
    """

    author_modules: tuple[str, ...] = ()
    experiment_system: str | None = None
    procedures: tuple[str, ...] = ()
    procedure_schedules: tuple[str, ...] = ()
    calibrations: str | None = None
    calibration_publications: str | None = None
    launch_provider: str | None = None
    comparison_provider: str | None = None
