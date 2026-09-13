"""Authoring-to-logical validation and diagnostic ownership."""

# pyright: reportUnusedFunction=false

from __future__ import annotations

from typing import Annotated

import pytest
from scopecat_testkit.authoring import bind_invocation, load_config
from scopecat_testkit.domain import domain_call

import scopecat as sc
from scopecat.compiler.frontend.logical_verification import verify_logical_program
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.graph_identity import ValueId
from scopecat.kernel.payloads import PayloadValue
from scopecat.kernel.problems import ProblemPhase, model_location
from scopecat.kernel.product_identity import ProductUse, ProductUseId
from scopecat.kernel.symbols import SymbolId
from scopecat.kernel.value_types import Float, Payload, Scalar
from scopecat.program.bindings import requires, resource_port
from scopecat.program.domain import domain_program
from scopecat.program.expressions import input_ref
from scopecat.program.logical import (
    LogicalProgram,
    ValueDef,
)
from scopecat.program.point_domain import point_axis_values
from scopecat.program.products import (
    ModuleProductDecl,
    ProductValueSpec,
    RecordSelection,
    entity_axis,
    product_axis,
    record_product,
)
from scopecat.program.values import compute as program_compute
from scopecat.program.values import input as program_input
from scopecat.sdk.instruments import InterfaceRef

_PLAY_WAVEFORMS = InterfaceRef("test.play_waveforms/v1")
_PLAY_WAVEFORMS_PLAY = _PLAY_WAVEFORMS.operation("play")
_PLAY_WAVEFORMS_PROGRAM = _PLAY_WAVEFORMS_PLAY.argument("program")
_SET_GAIN = InterfaceRef("test.set_gain/v1")
_SET_GAIN_VALUE = _SET_GAIN.property("value")


def _resolve(module: sc.ExperimentModule[None, ...]) -> None:
    @sc.experiment(id="test.graph", kind="graph")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(module())

    bind_invocation(
        experiment.build(),
        config_profile=load_config(),
    )


def _pair_values(*, upstream: object, parameter: object) -> tuple[object, object]:
    return upstream, parameter


def _identity_value(*, value: object) -> object:
    return value


def test_compute_graph_is_verified_before_parameter_contracts() -> None:
    missing = program_compute(
        "missing-producer",
        fn=lambda: 1.0,
        output_type=sc.ScalarType(sc.FloatType()),
    )
    missing_parameter = sc.parameter(
        "missing-parameter",
        sc.ScalarType(sc.FloatType()),
    )
    with pytest.raises(CheckFailed) as error:

        @sc.module(id="test.graph.order")
        def module(context: sc.ModuleContext) -> None:
            context.compute(
                "consumer",
                fn=_pair_values,
                inputs={
                    "upstream": missing.output,
                    "parameter": missing_parameter,
                },
                output_type=sc.ScalarType(sc.FloatType()),
            )

    assert error.value.problems[0].code == "module_compute_foreign_definition"


def test_invocation_rejects_an_unregistered_compute_output() -> None:
    missing = program_compute(
        "missing-program",
        fn=lambda: {"program": True},
        output_type=sc.ScalarType(sc.PayloadType("pulse-program")),
    )
    with pytest.raises(CheckFailed) as error:

        @sc.module(id="test.graph.invocation-missing")
        def module(context: sc.ModuleContext) -> None:
            drive = context._resource("drive", requires=(_PLAY_WAVEFORMS,))
            context._invoke(
                "play",
                resource=drive,
                operation=_PLAY_WAVEFORMS_PLAY,
                arguments={_PLAY_WAVEFORMS_PROGRAM: missing.output},
            )

    assert error.value.problems[0].code == "module_compute_foreign_definition"


def test_state_rejects_a_non_payload_compute_output() -> None:
    @sc.module(id="test.graph.state-type")
    def module(context: sc.ModuleContext) -> None:
        drive = context._resource("drive", requires=(_SET_GAIN,))
        compute_value = context.compute(
            "numeric-value",
            fn=lambda: 1.0,
            output_type=sc.ScalarType(sc.FloatType()),
        )
        context._bind_property(
            drive,
            _SET_GAIN_VALUE,
            value=compute_value,
        )

    with pytest.raises(CheckFailed) as error:
        _resolve(module)

    assert error.value.problems[0].code == "compute_payload_unavailable"
    assert error.value.problems[0].location == model_location("bindings", 0, "value")
    assert "numeric-value/outputs/result" in error.value.problems[0].message


def test_module_rejects_a_table_shaped_plan_state_binding() -> None:
    with pytest.raises(TypeError, match="scalar typed value or scalar literal"):

        @sc.module(id="test.graph.table-state-binding")
        def module(
            context: sc.ModuleContext,
            rows: Annotated[
                sc.Input[list[dict[str, object]]],
                sc.TableType(
                    columns=(sc.TableColumn("value", sc.ScalarType(sc.FloatType())),)
                ),
            ],
        ) -> None:
            drive = context._resource("drive", requires=(_SET_GAIN,))
            context._bind_property(
                drive,
                _SET_GAIN_VALUE,
                value=sc.input_ref(rows),
            )


def test_product_axes_reject_invalid_table_values_at_authoring_boundary() -> None:
    rows = program_input(
        "rows",
        sc.TableType(columns=(sc.TableColumn("value", sc.ScalarType(sc.FloatType())),)),
    )

    with pytest.raises(TypeError, match="axis values must be scalar"):
        product_axis("sample", size=rows)
    with pytest.raises(TypeError, match="exactly one entity column"):
        entity_axis("entity", rows)


def test_entity_axes_accept_one_column_entity_tables() -> None:
    rows = program_input(
        "rows",
        sc.TableType(
            columns=(
                sc.TableColumn(
                    "qubit",
                    sc.ScalarType(sc.EntityType(entity_kind="logical_qubit")),
                ),
            )
        ),
    )

    axis = entity_axis("entity", rows)

    assert axis.size is rows
    assert axis.kind == "entity"


def test_static_record_schema_is_checked_before_parameter_catalog() -> None:
    value_type = sc.ScalarType(sc.FloatType())
    missing_parameter = sc.parameter(
        "missing-record-parameter",
        value_type,
    )
    duplicate_axis = product_axis("sample", size=2)

    @sc.module(id="test.graph.record-schema")
    def module(context: sc.ModuleContext) -> None:
        context._product("signal", axes=(duplicate_axis, duplicate_axis))

    program = domain_program(
        "consume-parameter",
        dialect_id="test",
        dialect_version="1",
        body=object(),
        compiler_inputs={"value": value_type},
    )

    @sc.experiment(id="test.graph.record-schema", kind="record-schema")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(module())
        experiment.use(
            domain_call(
                program,
                compiler_inputs={"value": missing_parameter},
            )
        )

    with pytest.raises(CheckFailed) as error:
        bind_invocation(experiment.build(), config_profile=load_config())

    assert error.value.problems[0].code == ("product_axis_duplicate")
    assert error.value.problems[0].location == model_location(
        "products", "record-schema", "signal", "axes"
    )


def test_product_rejects_duplicate_effective_dimensions() -> None:
    @sc.module(id="test.graph.duplicate-dimension")
    def module(context: sc.ModuleContext) -> None:
        context._product(
            "signal",
            axes=(
                product_axis("i", size=2, shared_as="sample"),
                product_axis("q", size=2, shared_as="sample"),
            ),
        )

    with pytest.raises(CheckFailed) as error:
        _resolve(module)

    assert [problem.code for problem in error.value.problems] == [
        "product_axis_dimension_duplicate"
    ]


def test_resource_selector_rejects_external_operation_value() -> None:
    @sc.module(id="test.stage.resource-child")
    def child(
        context: sc.ModuleContext,
        subject: Annotated[sc.Input[sc.EntityRef | str], sc.EntityType()],
    ) -> None:
        context._resource("drive", for_entities=(sc.input_ref(subject),))

    @sc.module(id="test.stage.resource-parent")
    def parent(context: sc.ModuleContext) -> None:
        produce_subject = context.compute(
            "produce-subject",
            fn=lambda: "q0",
            output_type=sc.ScalarType(sc.EntityType()),
        )
        context.use(
            child.instantiate(
                "resource-child",
                subject=produce_subject,
            )
        )

    with pytest.raises(CheckFailed) as error:
        _resolve(parent)

    problem = error.value.problems[0]
    assert problem.code == "value_requires_execution"
    assert problem.location == model_location(
        "resources",
        "resource-parent",
        "resource-child",
        "drive",
        "selector",
        "entity_inputs",
        0,
    )
    assert "resource selector" in problem.message
    assert "external operation" in problem.message


def test_product_axis_rejects_external_operation_value() -> None:
    @sc.module(id="test.stage.record-execute")
    def module(context: sc.ModuleContext) -> None:
        size = context.compute(
            "axis-size",
            fn=lambda: 2,
            output_type=sc.ScalarType(sc.IntType()),
        )
        context._product(
            "signal",
            axes=(product_axis("sample", size=size),),
        )

    with pytest.raises(CheckFailed) as error:
        _resolve(module)

    problem = error.value.problems[0]
    assert problem.code == "product_axis_value_requires_execution"
    assert problem.location == model_location(
        "products", "record-execute", "signal", "axes", "sample", "size"
    )


def test_product_axis_rejects_point_dependent_value() -> None:
    size = sc.coordinate("axis-size", sc.IntType(minimum=1))

    @sc.module(id="test.stage.record-point")
    def module(
        context: sc.ModuleContext,
        size: Annotated[sc.Input[int], sc.IntType(minimum=1)],
    ) -> None:
        context._product(
            "signal",
            axes=(product_axis("sample", size=sc.input_ref(size)),),
        )

    @sc.experiment(id="test.stage.record-point", kind="graph")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(module(size))
        experiment.grid(sc.axis(size, (2, 3)))

    with pytest.raises(CheckFailed) as error:
        bind_invocation(
            experiment.build(),
            config_profile=load_config(),
        )

    problem = error.value.problems[0]
    assert problem.code == "product_axis_value_depends_on_point"
    assert problem.location == model_location(
        "products", "record-point", "signal", "axes", "sample", "size"
    )


def test_direct_compute_edge_is_topologically_ordered() -> None:
    value_type = sc.ScalarType(sc.FloatType())

    @sc.module(id="test.graph.direct-edge")
    def module(context: sc.ModuleContext) -> None:
        producer = context.compute(
            "producer",
            fn=lambda: 1.0,
            output_type=value_type,
        )
        context.compute(
            "consumer",
            fn=_identity_value,
            inputs={"value": producer},
            output_type=value_type,
        )

    @sc.experiment(id="test.graph.direct-edge", kind="graph")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(module())

    compiled = compile_invocation(experiment.build())

    assert [
        operation.id.local_id for operation in compiled.program.program.compute_nodes
    ] == [
        "producer",
        "consumer",
    ]


def test_compile_preserves_request_input_and_normalizes_logical_input() -> None:
    @sc.experiment(id="test.graph.verified-source", kind="graph")
    def experiment(
        experiment: sc.ExperimentContext,
        subject: Annotated[sc.Input[sc.EntityRef | str], sc.EntityType()],
    ) -> None:
        del experiment, subject

    compiled = compile_invocation(experiment.build(subject="q0"))

    assert compiled.request.inputs == {"subject": "q0"}
    assert compiled.program.program.inputs == {"subject": EntityRef(id="q0")}


def test_compile_invocation_projects_request_metadata() -> None:
    @sc.experiment(id="test.graph.prepared-request", kind="graph")
    def experiment(
        experiment: sc.ExperimentContext,
        subject: Annotated[sc.Input[sc.EntityRef | str], sc.EntityType()],
    ) -> None:
        del experiment, subject

    invocation = experiment.build(subject="q0")

    compiled = compile_invocation(
        invocation,
        metadata={"sample": "q0"},
        operator="alice",
    )

    assert compiled.request.experiment_id == invocation.definition.id
    assert compiled.request.inputs == {"subject": "q0"}
    assert compiled.request.metadata == {"sample": "q0"}
    assert compiled.request.operator == "alice"


def test_resource_selector_requires_a_scalar_entity_value() -> None:
    invalid_entity = program_input("subject", sc.ScalarType(sc.StringType()))
    port = resource_port(
        "drive",
        requires(for_entities=(invalid_entity,)),
    )

    with pytest.raises(CheckFailed) as error:
        verify_logical_program(
            LogicalProgram(
                experiment_id="test.graph.resource-selector",
                kind="graph",
                resource_ports=(port,),
            )
        )

    problem = error.value.problems[0]
    assert problem.code == "module_resource_entity_input_invalid"
    assert problem.phase is ProblemPhase.AUTHORING
    assert problem.location == model_location(
        "resources", "drive", "selector", "entity_inputs", 0
    )


def test_logical_verifier_owns_expression_proofs() -> None:
    value_id = ValueId(SymbolId(local_id="missing-input"))
    program = LogicalProgram(
        experiment_id="test.graph.expression-proof",
        kind="graph",
        value_defs=(
            ValueDef(
                id=value_id,
                value_type=Scalar(Float()),
                source=input_ref("missing", Scalar(Float())),
            ),
        ),
    )

    with pytest.raises(CheckFailed) as error:
        verify_logical_program(program)

    [problem] = error.value.problems
    assert problem.code == "expression_unknown_input"
    assert problem.location == model_location(
        "logical_program",
        "values",
        value_id.qualified_name,
    )


def test_source_coordinate_collision_ignores_non_coordinate_payload() -> None:
    point_source = point_axis_values(
        "payload",
        Scalar(Payload("point-payload")),
        (PayloadValue(schema_id="point-payload", payload={}),),
    )

    verify_logical_program(
        LogicalProgram(
            experiment_id="test.graph.payload-collision",
            kind="graph",
            point_domain=(point_source,),
            product_declarations=(
                ModuleProductDecl(id="payload", value_spec=ProductValueSpec()),
            ),
            record_selections=(record_product("payload"),),
        )
    )


def test_recording_rejects_an_unknown_product() -> None:
    with pytest.raises(CheckFailed) as error:
        verify_logical_program(
            LogicalProgram(
                experiment_id="test.graph.unknown-record-product",
                kind="graph",
                record_selections=(record_product("missing"),),
            )
        )

    assert [problem.code for problem in error.value.problems] == [
        "module_product_unknown"
    ]


def test_recording_rejects_one_use_identity_for_two_products() -> None:
    signal = ModuleProductDecl("signal", value_spec=ProductValueSpec())
    phase = ModuleProductDecl("phase", value_spec=ProductValueSpec())
    shared_id = ProductUseId("shared-use")

    with pytest.raises(CheckFailed) as error:
        verify_logical_program(
            LogicalProgram(
                experiment_id="test.graph.conflicting-product-use",
                kind="graph",
                product_declarations=(signal, phase),
                record_selections=(
                    RecordSelection(
                        product_use=ProductUse(
                            product_id=signal.product_id,
                            id=shared_id,
                        ),
                    ),
                    RecordSelection(
                        product_use=ProductUse(
                            product_id=phase.product_id,
                            id=shared_id,
                        ),
                    ),
                ),
            )
        )

    assert [problem.code for problem in error.value.problems] == [
        "product_use_identity_conflict"
    ]
    assert error.value.problems[0].phase is ProblemPhase.AUTHORING


def test_source_coordinate_collision_uses_typed_coordinate_predicate() -> None:
    point_source = point_axis_values(
        "coordinate",
        Scalar(Float()),
        (1.0,),
    )

    with pytest.raises(CheckFailed) as error:
        verify_logical_program(
            LogicalProgram(
                experiment_id="test.graph.coordinate-collision",
                kind="graph",
                point_domain=(point_source,),
                product_declarations=(
                    ModuleProductDecl(id="coordinate", value_spec=ProductValueSpec()),
                ),
                record_selections=(record_product("coordinate"),),
            )
        )

    assert error.value.problems[0].code == ("experiment_record_coordinate_collision")
