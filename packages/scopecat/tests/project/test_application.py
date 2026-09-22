from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from pydantic import BaseModel, ConfigDict

from scopecat.api.calibration_finalizer import (
    CalibrationPublicationCandidate,
    CalibrationPublicationPlanningContext,
    CalibrationPublicationPolicy,
)
from scopecat.api.calibration_planner import CalibrationPlanningContext
from scopecat.api.calibration_policy import CalibrationPublicationPolicyRegistry
from scopecat.api.calibration_publication import CalibrationCohortPublicationPlan
from scopecat.api.lab import LabClient
from scopecat.api.procedure_planner import ProcedurePlanningContext
from scopecat.application.lab import LabApplication
from scopecat.automation import (
    CalibrationDependencyEvidence,
    CalibrationObservation,
    CalibrationRegistry,
    CalibrationTargetRef,
    IntervalOccurrence,
    IntervalTrigger,
    ProcedureRegistry,
    ProcedureScheduleDefinition,
    ProcedureScheduleRegistry,
    procedure,
)
from scopecat.automation.calibration_definition import CalibrationDefinition
from scopecat.config.registry.records import ConfigCompositionPolicyRef
from scopecat.daemon.client import DaemonClient


class _ExampleIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: int


class _CalibrationInputs(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: int


@procedure(
    id="tests.application.example",
    version="1",
    intent=_ExampleIntent,
)
def _example_procedure(_context: object, _intent: _ExampleIntent) -> None:
    pass


@procedure(
    id="tests.application.other",
    version="1",
    intent=_ExampleIntent,
)
def _other_procedure(_context: object, _intent: _ExampleIntent) -> None:
    pass


def _build_intent(
    _context: ProcedurePlanningContext,
    occurrence: IntervalOccurrence,
) -> _ExampleIntent:
    return _ExampleIntent(value=occurrence.ordinal)


_SCHEDULE = ProcedureScheduleDefinition(
    id="tests.application.interval",
    version="1",
    procedure=_example_procedure,
    trigger=IntervalTrigger(
        anchor=datetime(2026, 8, 18, tzinfo=UTC),
        every=timedelta(days=1),
    ),
    _build_intent=_build_intent,
)


def _select_calibrations(
    _context: CalibrationPlanningContext,
) -> tuple[CalibrationTargetRef, ...]:
    return (CalibrationTargetRef(kind="qubit", id="q0"),)


def _observe_calibration(
    _context: CalibrationPlanningContext,
    _target: CalibrationTargetRef,
) -> CalibrationObservation[_CalibrationInputs]:
    return CalibrationObservation(inputs=_CalibrationInputs(value=1))


def _build_calibration_intent(
    _context: CalibrationPlanningContext,
    _target: CalibrationTargetRef,
    inputs: _CalibrationInputs,
    _dependencies: tuple[CalibrationDependencyEvidence, ...],
) -> _ExampleIntent:
    return _ExampleIntent(value=inputs.value)


_CALIBRATION = CalibrationDefinition(
    id="tests.application.calibration",
    version="1",
    input_type=_CalibrationInputs,
    procedure=_example_procedure,
    fanout_scope="tests.application",
    max_in_flight=2,
    _select_targets=_select_calibrations,
    _observe=_observe_calibration,
    _build_intent=_build_calibration_intent,
)

_PUBLISHED_CALIBRATION = replace(
    _CALIBRATION,
    id="tests.application.published-calibration",
    success_policy="published_result",
)


def _unused_publication_prepare(
    _context: CalibrationPublicationPlanningContext,
    _candidate: CalibrationPublicationCandidate,
) -> CalibrationCohortPublicationPlan:
    raise AssertionError("application composition does not prepare publications")


_PUBLICATION_POLICY = CalibrationPublicationPolicy(
    id="tests.application.calibration-publication",
    version="1",
    calibration=_PUBLISHED_CALIBRATION.ref,
    composition_policy=ConfigCompositionPolicyRef(
        id="tests.application.composition",
        version="1",
        fingerprint=f"sha256:{'9' * 64}",
    ),
    actor="calibration-finalizer",
    note="publish exact cohort",
    _prepare=_unused_publication_prepare,
)


def test_application_canonicalizes_procedure_iterable() -> None:
    application = LabApplication(procedures=(_example_procedure,))

    assert isinstance(application.procedures, ProcedureRegistry)
    assert application.procedures.refs == (_example_procedure.ref,)
    assert application.procedures.resolve(_example_procedure.ref) is _example_procedure


def test_application_preserves_prebuilt_procedure_registry() -> None:
    registry = ProcedureRegistry((_example_procedure,))

    application = LabApplication(procedures=registry)
    replaced = replace(application)

    assert application.procedures is registry
    assert replaced.procedures is registry


def test_application_owns_validated_interval_schedule_registry() -> None:
    schedules = ProcedureScheduleRegistry((_SCHEDULE,))

    application = LabApplication(
        procedures=(_example_procedure,),
        procedure_schedules=schedules,
    )
    replaced = replace(application)

    assert application.procedure_schedules is schedules
    assert replaced.procedure_schedules is schedules


def test_application_rejects_schedule_target_missing_from_procedure_registry() -> None:
    with pytest.raises(LookupError, match="no procedure"):
        LabApplication(procedures=(_other_procedure,), procedure_schedules=(_SCHEDULE,))


def test_direct_lab_client_rejects_schedule_target_missing_from_registry() -> None:
    with pytest.raises(LookupError, match="no procedure"):
        LabClient(
            cast("DaemonClient", object()),
            procedures=ProcedureRegistry((_other_procedure,)),
            procedure_schedules=ProcedureScheduleRegistry((_SCHEDULE,)),
        )


def test_direct_lab_client_rejects_calibration_target_missing_from_registry() -> None:
    with pytest.raises(LookupError, match="no procedure"):
        LabClient(
            cast("DaemonClient", object()),
            procedures=ProcedureRegistry((_other_procedure,)),
            calibrations=CalibrationRegistry((_CALIBRATION,)),
        )


def test_direct_lab_client_retains_calibration_publication_registry() -> None:
    publications = CalibrationPublicationPolicyRegistry(
        (_PUBLICATION_POLICY,),
        active=(_PUBLICATION_POLICY.ref,),
    )
    lab = LabClient(
        cast("DaemonClient", object()),
        procedures=ProcedureRegistry((_example_procedure,)),
        calibrations=CalibrationRegistry((_PUBLISHED_CALIBRATION,)),
        calibration_publications=publications,
    )

    assert lab.calibrations.publication_registry is publications
