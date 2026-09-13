from __future__ import annotations

from typing import Annotated

import scopecat.authoring as authoring
from scopecat.execution.local.program import CollectOperation
from scopecat.kernel.entity import EntityRef
from scopecat_testkit.authoring import bind_invocation
from scopecat_testkit.local_materialization import operations_of_type
from scopecat_testkit.materialized_effects import materialized_effects_contract
from scopecat_testkit.routing import routing_config, routing_endpoint

from scopecat_instruments import NetworkSweepGroupTarget, network_sweep
from scopecat_instruments.members import NETWORK_SWEEP


def test_typed_each_resources_route_to_different_instruments() -> None:
    q0 = EntityRef(id="q0", kind="logical_device")
    q1 = EntityRef(id="q1", kind="logical_device")
    config = routing_config(
        instruments={"source-0": "vna", "source-1": "vna"},
        bindings=(
            routing_endpoint(
                instrument_id="source-0",
                interface_id=NETWORK_SWEEP.interface_id,
                entity_id=q0.id,
            ),
            routing_endpoint(
                instrument_id="source-1",
                interface_id=NETWORK_SWEEP.interface_id,
                entity_id=q1.id,
            ),
        ),
        extra_entities=(q1,),
    )

    @authoring.experiment(id="test.symbolic.each-routing", kind="symbolic_each")
    def experiment(
        context: authoring.ExperimentContext,
        points: Annotated[
            authoring.Input[int],
            authoring.IntType(minimum=1),
        ],
    ) -> None:
        analyzers = network_sweep(
            context,
            for_=authoring.each(q0, q1),
        )
        analyzers.ensure(NetworkSweepGroupTarget(points=points))
        traces = analyzers.sweep()
        context.stack_entities(
            traces.map(lambda result: result.s_parameter),
            record_id="s_parameter",
        )

    record_ids = tuple(
        selection.record_id
        for selection in experiment.bind().definition.record_selections
    )
    assert record_ids == ("s_parameter",)

    bound = bind_invocation(experiment.build(points=3), config_profile=config)
    assert tuple(record.id for record in bound.bindings.product_record_uses) == (
        "frequency",
        "s_parameter",
    )
    preview = materialized_effects_contract(
        bound,
        bound.environment.parameters,
        config=config,
    )
    operations = operations_of_type(preview, CollectOperation, point_index=0)
    assert all(
        tuple(request.result_id for request in operation.command.requests)
        == ("frequency", "s_parameter")
        for operation in operations
    )
    assert all(
        tuple(binding.request_id for binding in operation.result_bindings)
        == ("frequency", "s_parameter")
        for operation in operations
    )
    assert {
        operation.instrument_id: tuple(operation.command.requests[0].entity_ids)
        for operation in operations
    } == {"source-0": ("q0",), "source-1": ("q1",)}


def test_resource_roles_route_one_entity_to_two_equivalent_instruments() -> None:
    q0 = EntityRef(id="q0", kind="logical_device")
    config = routing_config(
        instruments={"drive-vna": "vna", "readout-vna": "vna"},
        bindings=(
            routing_endpoint(
                instrument_id="drive-vna",
                interface_id=NETWORK_SWEEP.interface_id,
                entity_id=q0.id,
                role_id="drive",
            ),
            routing_endpoint(
                instrument_id="readout-vna",
                interface_id=NETWORK_SWEEP.interface_id,
                entity_id=q0.id,
                role_id="readout",
            ),
        ),
    )

    @authoring.experiment(id="test.symbolic.role-routing", kind="symbolic_roles")
    def experiment(context: authoring.ExperimentContext) -> None:
        drive = network_sweep(
            context,
            for_=authoring.one(q0),
            role="drive",
        )
        readout = network_sweep(
            context,
            for_=authoring.one(q0),
            role="readout",
        )
        drive.ensure(points=3)
        readout.ensure(points=3)
        context.alias(drive.sweep())
        context.alias(readout.sweep())

    invocation = experiment.build()
    assert [
        port.selector.role.role_id for port in invocation.definition.interface.resources
    ] == ["drive", "readout"]

    bound = bind_invocation(invocation, config_profile=config)
    preview = materialized_effects_contract(
        bound,
        bound.environment.parameters,
        config=config,
    )
    operations = operations_of_type(preview, CollectOperation, point_index=0)

    assert {operation.instrument_id for operation in operations} == {
        "drive-vna",
        "readout-vna",
    }
    assert {
        (
            operation.resource.logical_port_id.local_id,
            operation.resource.requested_role.role_id,
            operation.resource.route_id,
            operation.resource.route_role_id,
        )
        for operation in operations
    } == {
        ("network_sweep", "drive", "drive-vna.drive", "drive"),
        ("network_sweep.2", "readout", "readout-vna.readout", "readout"),
    }
