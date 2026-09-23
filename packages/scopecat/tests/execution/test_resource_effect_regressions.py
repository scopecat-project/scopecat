from __future__ import annotations

from dataclasses import replace

import pytest
from scopecat_testkit.authoring import load_config, parameters
from scopecat_testkit.bound_program import (
    ProgramFixture,
    StateAssignmentFixture,
    bind_program_facts,
    instrument_acquisition,
    observable_product,
    program_fixture,
)
from scopecat_testkit.expressions import state_property, verified_scalar_expr
from scopecat_testkit.local_materialization import (
    LocalEffectInspection,
    materialize_local_execution,
    operations_of_type,
)
from scopecat_testkit.routing import routing_endpoint, routing_graph

from scopecat.compiler.bound_facts import (
    LogicalResourceRequirement,
    record_product,
)
from scopecat.compiler.point_domain import (
    PointDomain,
)
from scopecat.compiler.relations.context import ParameterRelationData
from scopecat.config.environment import build_config_environment
from scopecat.execution.local.program import (
    ApplyStateOperation,
    CollectOperation,
)
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.instrument_members import InterfaceRef
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.resource_identity import (
    LogicalResourcePortId,
    logical_resource_port_id,
)
from scopecat.kernel.value_types import Entity, Float, Scalar, String
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.measurements.products import ProductDef
from scopecat.program.expressions import (
    ParameterLookupUse,
    ScalarExpr,
    lit,
    parameter_lookup,
    point_col,
)
from scopecat.program.logical import AcquireEffect
from scopecat.program.point_domain import point_axis_values
from scopecat.records.config import (
    ConfigProfileSnapshot,
)


def _port(value: str) -> LogicalResourcePortId:
    return logical_resource_port_id(value)


def _number(value: float) -> ScalarExpr:
    return verified_scalar_expr(lit(value), expected_type=Scalar(Float()))


def _quantity(value: float, unit: str) -> ScalarExpr:
    value_type = Scalar(QuantityType(dimension="frequency", unit=unit))
    return verified_scalar_expr(
        lit(Quantity(value, unit), value_type),
        expected_type=value_type,
    )


def _entity(value: str) -> ScalarExpr:
    value_type = Scalar(Entity())
    return verified_scalar_expr(lit(value, value_type), expected_type=value_type)


def _unit_program(
    *,
    experiment_id: str,
    resource_requirements: tuple[LogicalResourceRequirement, ...] = (),
    state: tuple[StateAssignmentFixture, ...] = (),
    products: tuple[ProductDef, ...] = (),
    acquisitions: tuple[AcquireEffect, ...] = (),
) -> ProgramFixture:
    uses_and_records = tuple(record_product(product) for product in products)
    return program_fixture(
        point_domain=PointDomain(axes=()),
        resource_requirements=resource_requirements,
        state=state,
        product_defs=products,
        instrument_acquisitions=acquisitions,
        product_uses=tuple(item[0] for item in uses_and_records),
        record_uses=tuple(item[1] for item in uses_and_records),
    )


def _bind(
    program: ProgramFixture,
    *,
    config: ConfigProfileSnapshot,
) -> LocalEffectInspection:
    environment = replace(
        build_config_environment(config),
        parameters=parameters(),
    )
    return materialize_local_execution(bind_program_facts(program, environment))


def test_record_products_keep_their_exact_logical_resource_bindings() -> None:
    config = _same_instrument_record_config()
    left = _port("left")
    right = _port("right")
    direct = _port("direct")
    left_product = observable_product("left-result")
    right_product = observable_product("right-result")
    direct_product = observable_product("direct-result")
    program = _unit_program(
        experiment_id="per-product-resource-bindings",
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=left,
                capabilities=(InterfaceRef("test.measure_left/v1"),),
            ),
            LogicalResourceRequirement(
                port_id=right,
                capabilities=(InterfaceRef("test.measure_right/v1"),),
            ),
            LogicalResourceRequirement(
                port_id=direct,
                capabilities=(InterfaceRef("test.measure_direct/v1"),),
            ),
        ),
        products=(left_product, right_product, direct_product),
        acquisitions=(
            instrument_acquisition(
                left_product,
                resource_port_id=left,
                interface="test.measure_left/v1",
                result_id="left",
            ),
            instrument_acquisition(
                right_product,
                resource_port_id=right,
                interface="test.measure_right/v1",
                result_id="right",
            ),
            instrument_acquisition(
                direct_product,
                resource_port_id=direct,
                interface="test.measure_direct/v1",
                result_id="direct",
            ),
        ),
    )

    plan = _bind(program, config=config)
    requests = {
        request.id: request
        for operation in operations_of_type(plan, CollectOperation, point_index=0)
        for request in operation.command.requests
    }
    assert {
        key: (
            tuple(request.entity_ids),
            tuple(binding.channel_id for binding in request.channel_bindings),
        )
        for key, request in requests.items()
    } == {
        "left": (("q0",), ("drive-q0",)),
        "right": (("q1",), ("readout-q0",)),
        "direct": ((), ()),
    }


def test_each_effect_uses_only_its_explicit_interface_endpoints() -> None:
    config = load_config()
    routing = routing_graph(
        bindings=[
            routing_endpoint(
                instrument_id="source-0",
                interface_id="test.a/v1",
                entity_id="q0",
                channel_id="drive-q0",
                component_path=("channels", "drive-q0"),
            ),
            routing_endpoint(
                instrument_id="source-0",
                interface_id="test.b/v1",
                entity_id="q0",
                channel_id="readout-q0",
                component_path=("channels", "readout-q0"),
            ),
        ],
    )
    config = config.model_copy(
        update={"system": config.system.model_copy(update={"routing": routing})}
    )
    port = _port("combined")
    a_product = observable_product("A-result")
    b_product = observable_product("B-result")
    program = _unit_program(
        experiment_id="explicit-interface-endpoints",
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=port,
                capabilities=(InterfaceRef("test.a/v1"), InterfaceRef("test.b/v1")),
                entity_uses=(_entity("q0"),),
            ),
        ),
        state=(
            state_property(
                port,
                interface_id="test.a/v1",
                property_id="level",
                value=_number(1.0),
            ),
        ),
        products=(a_product, b_product),
        acquisitions=(
            instrument_acquisition(
                a_product,
                resource_port_id=port,
                interface="test.a/v1",
                result_id="A-result",
            ),
            instrument_acquisition(
                b_product,
                resource_port_id=port,
                interface="test.b/v1",
                result_id="B-result",
            ),
        ),
    )

    plan = _bind(program, config=config)

    [state] = operations_of_type(plan, ApplyStateOperation, point_index=0)
    assert state.instrument_id == "source-0"
    assert [
        (binding.interface_id, binding.channel_id)
        for binding in state.targets[0].channel_bindings
    ] == [("test.a/v1", "drive-q0")]
    assert state.targets[0].component_path == ("channels", "drive-q0")

    requests = {
        request.id: request
        for operation in operations_of_type(plan, CollectOperation, point_index=0)
        for request in operation.command.requests
    }
    assert {
        key: (
            request.interface_id,
            tuple(request.component_path),
            tuple(
                (binding.interface_id, binding.channel_id)
                for binding in request.channel_bindings
            ),
        )
        for key, request in requests.items()
    } == {
        "A-result": (
            "test.a/v1",
            ("channels", "drive-q0"),
            (("test.a/v1", "drive-q0"),),
        ),
        "B-result": (
            "test.b/v1",
            ("channels", "readout-q0"),
            (("test.b/v1", "readout-q0"),),
        ),
    }


def test_logical_state_bindings_reach_required_instrument() -> None:
    config = _split_instrument_config()
    source = _port("source")
    first_state = state_property(
        source,
        interface_id="test.set_level/v1",
        property_id="level",
        value=_number(1.0),
    )

    single_plan = _bind(
        _unit_program(
            experiment_id="logical-state-requirements",
            resource_requirements=(
                LogicalResourceRequirement(
                    port_id=source,
                    capabilities=(InterfaceRef("test.set_level/v1"),),
                    entity_uses=(_entity("q0"),),
                ),
            ),
            state=(first_state,),
        ),
        config=config,
    )
    target = operations_of_type(single_plan, ApplyStateOperation, point_index=0)[
        0
    ].targets[0]
    assert target.entity_ids == ("q0",)
    assert tuple(binding.channel_id for binding in target.channel_bindings) == (
        "drive-q0",
    )
    assert [
        (requirement.kind, requirement.id)
        for requirement in single_plan.resource_requirements
    ] == [("instrument", "source-0")]


def test_resource_entity_queries_retain_point_local_parameter_reads() -> None:
    text_type = Scalar(String())
    source = _port("source")
    lookup = ParameterLookupUse(
        table_id="routes",
        key_input_types=(("site", text_type),),
        literal_key_columns=frozenset(),
        column_id="entity",
        result_type=text_type,
    )
    program = program_fixture(
        point_domain=PointDomain(
            axes=(point_axis_values("site", text_type, ("left", "right")),)
        ),
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=source,
                capabilities=(InterfaceRef("test.set_level/v1"),),
                entity_uses=(
                    parameter_lookup(
                        lookup, key={"site": point_col("site", text_type)}
                    ),
                ),
            ),
        ),
        state=(
            state_property(
                source,
                interface_id="test.set_level/v1",
                property_id="level",
                value=_number(1.0),
            ),
        ),
    )
    environment = replace(
        build_config_environment(_split_instrument_config()),
        parameters=ParameterRelationData(
            tables={
                "routes": [
                    {"site": "left", "entity": "q0"},
                    {"site": "right", "entity": "q1"},
                ]
            }
        ),
    )
    plan = materialize_local_execution(bind_program_facts(program, environment))
    assert [read.evidence.keyed[0].cells[0].value for read in plan.parameter_reads] == [
        "q0",
        "q1",
    ]
    assert [read.point_ordinal for read in plan.parameter_reads] == [0, 1]
    for read in plan.parameter_reads:
        assert "resource_selection_not_captured" not in read.evidence.incomplete_reasons
        assert "resource_topology_not_captured" in read.evidence.incomplete_reasons


def test_logical_state_does_not_broadcast_across_instruments() -> None:
    config = _split_instrument_config()
    source = _port("source")
    program = _unit_program(
        experiment_id="ambiguous-logical-state-claim",
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=source,
                capabilities=(InterfaceRef("test.set_level/v1"),),
                entity_uses=(
                    _entity("q0"),
                    _entity("q1"),
                ),
            ),
        ),
        state=(
            state_property(
                source,
                interface_id="test.set_level/v1",
                property_id="level",
                value=_number(1.0),
            ),
        ),
    )

    with pytest.raises(CheckFailed) as failure:
        _bind(program, config=config)

    assert [problem.code for problem in failure.value.problems] == [
        "module_resource_route_not_found"
    ]


def test_entity_only_targets_survive_bound_and_execution_boundaries() -> None:
    config = _entity_only_config()
    signal = _port("signal")
    product = observable_product("signal-result")
    program = _unit_program(
        experiment_id="entity-only-targets",
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=signal,
                capabilities=(
                    InterfaceRef("test.set_level/v1"),
                    InterfaceRef("test.measure_signal/v1"),
                ),
                entity_uses=(_entity("q0"),),
            ),
        ),
        state=(
            state_property(
                signal,
                interface_id="test.set_level/v1",
                property_id="level",
                value=_number(1.0),
            ),
        ),
        products=(product,),
        acquisitions=(
            instrument_acquisition(
                product,
                resource_port_id=signal,
                interface="test.measure_signal/v1",
                result_id="signal",
            ),
        ),
    )

    plan = _bind(program, config=config)
    execution = plan

    state_target = operations_of_type(plan, ApplyStateOperation, point_index=0)[
        0
    ].targets[0]
    request = operations_of_type(plan, CollectOperation, point_index=0)[
        0
    ].command.requests[0]
    assert (state_target.entity_ids, state_target.channel_bindings) == (("q0",), ())
    assert (request.entity_ids, request.channel_bindings) == (["q0"], [])

    target = operations_of_type(execution, ApplyStateOperation, point_index=0)[
        0
    ].targets[0]
    request = operations_of_type(execution, CollectOperation, point_index=0)[
        0
    ].command.requests[0]
    assert (target.entity_ids, target.channel_bindings) == (("q0",), ())
    assert (tuple(request.entity_ids), tuple(request.channel_bindings)) == (("q0",), ())


def test_equal_demands_for_one_physical_state_owner_are_coalesced() -> None:
    config = _shared_component_config()
    left = _port("left")
    right = _port("right")
    program = _unit_program(
        experiment_id="aliased-logical-state-target",
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=left,
                capabilities=(InterfaceRef("test.set_frequency/v1"),),
                entity_uses=(_entity("q0"),),
            ),
            LogicalResourceRequirement(
                port_id=right,
                capabilities=(InterfaceRef("test.set_frequency/v1"),),
                entity_uses=(_entity("q1"),),
            ),
        ),
        state=(
            state_property(
                left,
                interface_id="test.set_frequency/v1",
                property_id="frequency",
                value=_quantity(5.0, "GHz"),
            ),
            state_property(
                right,
                interface_id="test.set_frequency/v1",
                property_id="frequency",
                value=_quantity(5000.0, "MHz"),
            ),
        ),
    )

    plan = _bind(program, config=config)

    [operation] = operations_of_type(plan, ApplyStateOperation, point_index=0)
    assert len(operation.targets) == 1
    [target] = operation.targets
    assert target.component_path == ("lo_groups", "lo0")
    assert target.entity_ids == ("q0", "q1")
    assert [binding.channel_id for binding in target.channel_bindings] == [
        "channel-q0",
        "channel-q1",
    ]
    assert [
        origin.resource.logical_port_id.qualified_name for origin in target.origins
    ] == ["left", "right"]


def test_conflicting_demands_for_one_physical_state_owner_are_rejected() -> None:
    config = _shared_component_config()
    left = _port("left")
    right = _port("right")
    program = _unit_program(
        experiment_id="conflicting-physical-state-owner",
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=left,
                capabilities=(InterfaceRef("test.set_frequency/v1"),),
                entity_uses=(_entity("q0"),),
            ),
            LogicalResourceRequirement(
                port_id=right,
                capabilities=(InterfaceRef("test.set_frequency/v1"),),
                entity_uses=(_entity("q1"),),
            ),
        ),
        state=(
            state_property(
                left,
                interface_id="test.set_frequency/v1",
                property_id="frequency",
                value=_quantity(5.0, "GHz"),
            ),
            state_property(
                right,
                interface_id="test.set_frequency/v1",
                property_id="frequency",
                value=_quantity(5.1, "GHz"),
            ),
        ),
    )

    with pytest.raises(CheckFailed) as failure:
        _bind(program, config=config)

    assert "experiment_conflicting_desired_state" in {
        problem.code for problem in failure.value.problems
    }
    [problem] = [
        problem
        for problem in failure.value.problems
        if problem.code == "experiment_conflicting_desired_state"
    ]
    assert "q0 via left" in problem.message
    assert "q1 via right" in problem.message


def _same_instrument_record_config() -> ConfigProfileSnapshot:
    config = load_config()
    topology = config.topology.model_copy(
        update={
            "entities": [
                *config.topology.entities,
                EntityRef(id="q1", kind="logical_device"),
            ]
        }
    )
    routing = routing_graph(
        bindings=[
            routing_endpoint(
                instrument_id="source-0",
                interface_id="test.measure_left/v1",
                entity_id="q0",
                channel_id="drive-q0",
            ),
            routing_endpoint(
                instrument_id="source-0",
                interface_id="test.measure_right/v1",
                entity_id="q1",
                channel_id="readout-q0",
            ),
            routing_endpoint(
                instrument_id="source-0",
                interface_id="test.measure_direct/v1",
            ),
        ],
    )
    return config.model_copy(
        update={
            "system": config.system.model_copy(
                update={"topology": topology, "routing": routing}
            )
        }
    )


def _split_instrument_config() -> ConfigProfileSnapshot:
    config = load_config()
    source = config.instrument_registry.instruments[0]
    topology = config.topology.model_copy(
        update={
            "entities": [
                *config.topology.entities,
                EntityRef(id="q1", kind="logical_device"),
            ],
        }
    )
    routing = routing_graph(
        bindings=[
            routing_endpoint(
                instrument_id="source-0",
                interface_id="test.set_level/v1",
                entity_id="q0",
                channel_id="drive-q0",
            ),
            routing_endpoint(
                instrument_id="source-1",
                interface_id="test.set_level/v1",
                entity_id="q1",
                channel_id="readout-q0",
            ),
        ],
    )
    system = config.system.model_copy(
        update={
            "topology": topology,
            "instrument_registry": config.instrument_registry.model_copy(
                update={
                    "instruments": [
                        source,
                        source.model_copy(
                            update={
                                "id": "source-1",
                                "exclusivity_key": "source-1",
                            }
                        ),
                    ]
                }
            ),
            "routing": routing,
        }
    )
    return config.model_copy(update={"system": system})


def _entity_only_config() -> ConfigProfileSnapshot:
    config = load_config()
    routing = routing_graph(
        bindings=[
            routing_endpoint(
                instrument_id="source-0",
                interface_id=interface_id,
                entity_id="q0",
            )
            for interface_id in (
                "test.set_level/v1",
                "test.measure_signal/v1",
            )
        ],
    )
    return config.model_copy(
        update={"system": config.system.model_copy(update={"routing": routing})}
    )


def _shared_component_config() -> ConfigProfileSnapshot:
    config = load_config()
    topology = config.topology.model_copy(
        update={
            "entities": [
                *config.topology.entities,
                EntityRef(id="q1", kind="logical_device"),
            ]
        }
    )
    routing = routing_graph(
        bindings=[
            routing_endpoint(
                instrument_id="source-0",
                interface_id="test.set_frequency/v1",
                entity_id=entity_id,
                channel_id=f"channel-{entity_id}",
                component_path=("lo_groups", "lo0"),
            )
            for entity_id in ("q0", "q1")
        ]
    )
    return config.model_copy(
        update={
            "system": config.system.model_copy(
                update={"topology": topology, "routing": routing}
            )
        }
    )
