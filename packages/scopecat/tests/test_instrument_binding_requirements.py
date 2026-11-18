"""Unsupported demands remain explicit instead of receiving invented topology."""

from typing import cast

from scopecat_instruments import NetworkSweepProducts, network_sweep

import scopecat as sc
from scopecat.application.instrument_bindings import inspect_instrument_requirements
from scopecat.authoring.experiments import Experiment
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.problems import Problem, ProblemPhase


def inspect(declaration: Experiment[..., object]):
    return inspect_instrument_requirements(
        declaration,
        inputs={},
        serialized_inputs={},
        workspace_id="test",
        code_revision=None,
        version="v1",
    )


def test_no_role_is_invented_and_duplicate_demands_need_author_intent() -> None:
    @sc.experiment
    def ordinary(experiment: sc.ExperimentContext) -> NetworkSweepProducts:
        device = network_sweep(experiment)
        device.ensure(points=3)
        return device.sweep()

    report = inspect(ordinary)
    assert report.supported
    assert report.requirements[0].role_id is None

    @sc.experiment
    def ambiguous(experiment: sc.ExperimentContext) -> NetworkSweepProducts:
        first = network_sweep(experiment)
        second = network_sweep(experiment)
        first.ensure(points=3)
        second.ensure(points=3)
        return second.sweep()

    rejected = inspect(ambiguous)
    assert not rejected.supported
    assert "same interface and role" in rejected.diagnostics[0].message


def test_entity_dependent_demands_are_not_replaced_with_dummy_topology() -> None:
    from scopecat.kernel.entity import EntityRef

    @sc.experiment
    def entity(experiment: sc.ExperimentContext) -> NetworkSweepProducts:
        device = network_sweep(
            experiment, for_=sc.one(EntityRef(id="q0", kind="qubit"))
        )
        device.ensure(points=3)
        return device.sweep()

    rejected = inspect(entity)
    assert not rejected.supported
    assert not rejected.requirements
    assert "topology" in rejected.diagnostics[0].message


def test_symbolic_check_failure_is_a_structured_unavailable_report() -> None:
    @sc.experiment
    def unavailable(experiment: sc.ExperimentContext) -> None:
        del experiment
        raise CheckFailed(
            [
                Problem(
                    phase=ProblemPhase.AUTHORING,
                    code="missing_topology",
                    message="Choose the intended topology",
                )
            ]
        )

    rejected = inspect(cast("Experiment[..., object]", unavailable))
    assert not rejected.supported
    assert "missing_topology" in rejected.diagnostics[0].message


def test_typed_inputs_use_the_validated_json_identity() -> None:
    from datetime import date

    @sc.experiment
    def ordinary(
        experiment: sc.ExperimentContext, *, day: date
    ) -> NetworkSweepProducts:
        assert day.year == 2026
        device = network_sweep(experiment)
        device.ensure(points=3)
        return device.sweep()

    report = inspect_instrument_requirements(
        ordinary,
        inputs={"day": date(2026, 11, 18)},
        serialized_inputs={"day": "2026-11-18"},
        workspace_id="test",
        code_revision=None,
        version="v1",
    )
    assert report.supported
