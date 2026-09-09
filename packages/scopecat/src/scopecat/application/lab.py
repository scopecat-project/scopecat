"""User-owned execution composition for notebooks and project workers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from scopecat.api.calibration_policy import CalibrationPublicationPolicyRegistry
from scopecat.application.launch import LaunchProvider
from scopecat.automation.calibration_definition import CalibrationRegistry
from scopecat.automation.definition import ProcedureRegistry
from scopecat.automation.intervals import ProcedureScheduleRegistry

if TYPE_CHECKING:
    from scopecat.api.calibration_planner import CalibrationPlanningContext
    from scopecat.api.calibration_policy import (
        CalibrationPublicationPolicyRegistration,
    )
    from scopecat.api.lab import LabClient
    from scopecat.api.procedure_planner import ProcedurePlanningContext
    from scopecat.application.authoring import AuthorExperiments
    from scopecat.automation.calibration_definition import RegisteredCalibration
    from scopecat.automation.definition import RegisteredProcedure
    from scopecat.automation.intervals import RegisteredProcedureSchedule
    from scopecat.planning.system import ExperimentSystemBuilder


@dataclass(frozen=True, slots=True, init=False)
class LabApplication:
    """Version-controlled executable composition for one lab project.

    The application owns notebook and worker execution capabilities. Daemon
    bootstrap configuration and instrument backend composition are declared
    separately so the server process does not import these execution callbacks.

    Calibration publication capabilities may retain historical policy versions
    for already-admitted cohorts. Their registry separately selects the exact
    active bindings used when this application admits new cohorts.
    """

    launch_provider: LaunchProvider | None = field(default=None, repr=False)
    authors: AuthorExperiments | None = field(default=None, repr=False)

    build_experiment_system: ExperimentSystemBuilder | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    procedures: ProcedureRegistry = field(
        default_factory=ProcedureRegistry,
        repr=False,
    )
    procedure_schedules: ProcedureScheduleRegistry[ProcedurePlanningContext] = field(
        default_factory=ProcedureScheduleRegistry,
        repr=False,
    )
    calibrations: CalibrationRegistry[CalibrationPlanningContext] = field(
        default_factory=CalibrationRegistry,
        repr=False,
    )
    calibration_publications: CalibrationPublicationPolicyRegistry = field(
        default_factory=CalibrationPublicationPolicyRegistry,
        repr=False,
    )

    def __init__(
        self,
        build_experiment_system: ExperimentSystemBuilder | None = None,
        procedures: Iterable[RegisteredProcedure] | ProcedureRegistry = (),
        procedure_schedules: (
            Iterable[RegisteredProcedureSchedule[ProcedurePlanningContext]]
            | ProcedureScheduleRegistry[ProcedurePlanningContext]
        ) = (),
        calibrations: (
            Iterable[RegisteredCalibration[CalibrationPlanningContext]]
            | CalibrationRegistry[CalibrationPlanningContext]
        ) = (),
        calibration_publications: (
            Iterable[CalibrationPublicationPolicyRegistration]
            | CalibrationPublicationPolicyRegistry
        ) = (),
        launch_provider: LaunchProvider | None = None,
        author_modules: tuple[str, ...] = (),
    ) -> None:
        object.__setattr__(
            self,
            "build_experiment_system",
            build_experiment_system,
        )
        authors = None
        if author_modules:
            from scopecat.application.authoring import AuthorExperiments

            authors = AuthorExperiments.discover(*author_modules)
            launch_provider = authors.compose(launch_provider)
            procedures = (
                (*procedures.values(), *authors.procedures)
                if isinstance(procedures, ProcedureRegistry)
                else (*procedures, *authors.procedures)
            )
        object.__setattr__(self, "authors", authors)
        object.__setattr__(self, "launch_provider", launch_provider)
        procedure_registry = (
            procedures
            if isinstance(procedures, ProcedureRegistry)
            else ProcedureRegistry(procedures)
        )
        schedule_registry = (
            procedure_schedules
            if isinstance(procedure_schedules, ProcedureScheduleRegistry)
            else ProcedureScheduleRegistry(procedure_schedules)
        )
        for schedule in schedule_registry.values():
            procedure_registry.resolve(schedule.procedure.ref)
        calibration_registry = (
            calibrations
            if isinstance(calibrations, CalibrationRegistry)
            else CalibrationRegistry(calibrations)
        )
        for definition in calibration_registry.values():
            procedure_registry.resolve(definition.procedure.ref)
        publication_registry = (
            calibration_publications
            if isinstance(
                calibration_publications,
                CalibrationPublicationPolicyRegistry,
            )
            else CalibrationPublicationPolicyRegistry(calibration_publications)
        )
        object.__setattr__(self, "procedures", procedure_registry)
        object.__setattr__(self, "procedure_schedules", schedule_registry)
        object.__setattr__(self, "calibrations", calibration_registry)
        object.__setattr__(
            self,
            "calibration_publications",
            publication_registry,
        )

    def connect(
        self,
        daemon: str,
        *,
        operator: str = "operator",
    ) -> LabClient:
        """Connect notebook code while retaining locally authored closures."""

        from scopecat.api.lab import LabClient

        return LabClient(
            daemon,
            build_experiment_system=self.build_experiment_system,
            procedures=self.procedures,
            procedure_schedules=self.procedure_schedules,
            calibrations=self.calibrations,
            calibration_publications=self.calibration_publications,
            operator=operator,
        )


__all__ = [
    "LabApplication",
]
