# pyright: reportUnusedFunction=false

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import pytest
from scopecat_testkit.authoring import bind_invocation
from scopecat_testkit.local_materialization import materialize_local_execution
from scopecat_testkit.materialized_effects import config_with_physical_resources

import scopecat as sc
from scopecat.authoring._module_context import DefinitionResource
from scopecat.authoring.member_projection import StateProjector, StateTarget
from scopecat.execution.local.program import ApplyStateOperation
from scopecat.planning.local_materialization import (
    materialize_local_success_state,
    prepare_local_target,
)
from scopecat.program.bindings import EnsureStateIntent
from scopecat.program.logical import LogicalEnsureState
from scopecat.program.state import StateBinding
from scopecat.sdk.instruments import InterfaceRef, PropertyRef

_SOURCE = InterfaceRef("test.state_effect_source/v1")
_SOURCE_LEVEL = _SOURCE.property("level")
_SOURCE_ENABLED = _SOURCE.property("enabled")


def _source_assignments(
    *,
    level: StateBinding,
    enabled: StateBinding,
) -> dict[PropertyRef, StateBinding]:
    return {
        _SOURCE_LEVEL: level,
        _SOURCE_ENABLED: enabled,
    }


@dataclass(frozen=True)
class _DeclaredSourceState:
    level: StateBinding
    enabled: StateBinding


@dataclass(frozen=True)
class _TypedSource:
    resource: DefinitionResource

    def state_targets(
        self,
        state: _DeclaredSourceState,
        /,
    ) -> tuple[StateTarget, ...]:
        return (
            (
                self.resource,
                _source_assignments(level=state.level, enabled=state.enabled),
            ),
        )


def test_ensure_binds_one_declarative_target_with_point_resolved_values() -> None:
    @sc.module(id="test.state-effect")
    def state_effect(
        module: sc.ModuleContext,
        level: Annotated[
            sc.Input[sc.Quantity],
            sc.ScalarType(sc.QuantityType(unit="V")),
        ],
    ) -> None:
        source = module._resource("source", requires=(_SOURCE,))
        module._ensure(
            source,
            _source_assignments(level=level, enabled=True),
        )

    body = state_effect.definition.body
    assert [binding.property_id for binding in body.bindings] == [
        "level",
        "enabled",
    ]
    assert isinstance(body.bindings[0].value, sc.ValueRef)
    assert body.bindings[1].value is True
    [effect] = body.effects
    assert isinstance(effect, EnsureStateIntent)
    assert len(effect.assignments) == 2


def test_ensure_remains_one_coherent_effect_through_local_planning() -> None:
    @sc.module(id="test.coherent-target")
    def module(context: sc.ModuleContext) -> None:
        source = context._resource("source", requires=(_SOURCE,))
        context._ensure(
            source,
            _source_assignments(level=1.5, enabled=True),
        )

    @sc.experiment(id="test.coherent-target", kind="state-effect")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(module())

    bound = bind_invocation(
        experiment(),
        config_profile=config_with_physical_resources(
            {"source-device": (_SOURCE.interface_id,)}
        ),
    )

    [effect] = bound.program.program.effects
    assert isinstance(effect, LogicalEnsureState)
    assert [assignment.property_id for assignment in effect.assignments] == [
        "level",
        "enabled",
    ]

    plan = materialize_local_execution(bound)
    operations = tuple(
        effect.operation
        for effect in plan.effects
        if isinstance(effect.operation, ApplyStateOperation)
    )
    assert len(operations) == 1
    assert [target.property_id for target in operations[0].targets] == [
        "level",
        "enabled",
    ]


def test_adjacent_ensure_calls_remain_separate_state_effects() -> None:
    @sc.module(id="test.sequential-targets")
    def module(context: sc.ModuleContext) -> None:
        source = context._resource("source", requires=(_SOURCE,))
        context._ensure(
            source,
            _source_assignments(level=1.0, enabled=True),
        )
        context._ensure(
            source,
            _source_assignments(level=2.0, enabled=False),
        )

    @sc.experiment(id="test.sequential-targets", kind="state-effect")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(module())

    bound = bind_invocation(
        experiment(),
        config_profile=config_with_physical_resources(
            {"source-device": (_SOURCE.interface_id,)}
        ),
    )

    assert all(
        isinstance(effect, LogicalEnsureState)
        for effect in bound.program.program.effects
    )
    plan = materialize_local_execution(bound)
    operations = tuple(
        effect.operation
        for effect in plan.effects
        if isinstance(effect.operation, ApplyStateOperation)
    )
    assert len(operations) == 2
    assert [operation.targets[0].value.root for operation in operations] == [1.0, 2.0]


def test_root_success_state_is_materialized_outside_point_effects() -> None:
    @sc.experiment(id="test.success_state", kind="state-effect")
    def experiment_definition(
        experiment: sc.ExperimentContext,
        level: sc.Input[float] = 0.0,
    ) -> None:
        source = experiment._resource("source", requires=(_SOURCE,))
        experiment.on_success(
            _TypedSource(source),
            _DeclaredSourceState(level=level, enabled=False),
        )

    bound = bind_invocation(
        experiment_definition(),
        config_profile=config_with_physical_resources(
            {"source-device": (_SOURCE.interface_id,)}
        ),
    )

    assert bound.program.program.effects == ()
    success_state = bound.program.program.success_state
    assert isinstance(success_state, LogicalEnsureState)
    assert [assignment.property_id for assignment in success_state.assignments] == [
        "level",
        "enabled",
    ]
    target = prepare_local_target(
        bound,
        product_use_ids=frozenset(),
        instrument_order=("source-device",),
    )
    [operation] = materialize_local_success_state(bound, target=target)
    assert operation.instrument_id == "source-device"
    assert [target.property_id for target in operation.targets] == [
        "level",
        "enabled",
    ]
    assert operation.targets[0].value.root == 0.0


def test_root_success_state_accepts_a_typed_state_projector() -> None:
    @sc.experiment(id="test.typed-final-state", kind="state-effect")
    def experiment_definition(experiment: sc.ExperimentContext) -> None:
        source = experiment._resource("source", requires=(_SOURCE,))
        typed_source: StateProjector[_DeclaredSourceState] = _TypedSource(source)
        experiment.on_success(
            typed_source,
            _DeclaredSourceState(level=0.0, enabled=False),
        )

    success_state = experiment_definition.bind().definition.success_state
    assert success_state is not None
    assert [assignment.property_id for assignment in success_state.assignments] == [
        "level",
        "enabled",
    ]


def test_on_success_state_rejects_point_coordinates() -> None:
    level = sc.coordinate("level", sc.ScalarType(sc.FloatType()))

    with pytest.raises(
        ValueError,
        match="on_success state cannot depend on point coordinates",
    ):

        @sc.experiment(id="test.success_state-coordinate", kind="state-effect")
        def experiment_definition(experiment: sc.ExperimentContext) -> None:
            source = experiment._resource("source", requires=(_SOURCE,))
            experiment.on_success(
                _TypedSource(source),
                _DeclaredSourceState(level=level, enabled=False),
            )

        experiment_definition()
