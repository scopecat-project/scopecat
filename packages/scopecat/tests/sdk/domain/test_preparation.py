from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from scopecat_testkit.authoring import bind_invocation, load_config
from scopecat_testkit.domain import domain_call

import scopecat as sc
from scopecat.kernel.errors import ProviderContractError
from scopecat.kernel.state import StateValue
from scopecat.measurements.results import MeasurementScalar
from scopecat.measurements.values import MeasurementValueCandidate
from scopecat.planning.domain_bridge import (
    make_domain_batch_request,
    make_domain_call_view,
)
from scopecat.planning.domain_results import domain_result_product_use_ids
from scopecat.planning.point_materialization import (
    prepare_bound_points,
)
from scopecat.program.domain import domain_program
from scopecat.program.measurement_types import MeasurementDType
from scopecat.program.products import ModuleProductDecl, ProductValueSpec
from scopecat.sdk.domain import (
    DomainBatchCandidate,
    DomainBatchInputs,
    DomainBatchRequest,
    DomainPreparationBuilder,
    DomainResidencyAddress,
    DomainResidencyRequirement,
    DomainResultBinding,
    DomainResultMapping,
    DomainStateAddress,
    DomainStateRequirement,
)
from scopecat.sdk.domain.execution import PreparedDomainExecution
from scopecat.sdk.domain.invocation import (
    DomainOutputValue,
    stream_domain_output_values,
)
from scopecat.sdk.domain.job import (
    DomainInvocationSpec,
    DomainResultValue,
)
from scopecat.sdk.domain.parameter_evidence import (
    attach_domain_input_reads,
    read_domain_input_reads,
)
from scopecat.sdk.domain.runtime import (
    DomainExecutionReceipt,
    DomainExecutionResult,
)

type _ResultBinding = DomainResultBinding[str]


class _NoEffectsRuntime:
    def start(
        self,
        execution_key: str,
        payload: dict[str, str],
        *,
        instruments: object,
    ) -> DomainExecutionReceipt | DomainExecutionResult[dict[str, str]]:
        del execution_key, payload, instruments
        raise AssertionError("preparation must not execute")


class _NoEffectsSetup:
    def prepare(
        self,
        execution_key: str,
        payload: dict[str, str],
        *,
        instruments: object,
    ) -> None:
        del execution_key, payload, instruments
        raise AssertionError("preparation must not execute")


def _preparation_context(
    tmp_path: Path,
    *,
    namespace: str,
    shared_product_uses: bool = False,
    dtype: MeasurementDType = "int64",
    unit: str | None = "count",
) -> DomainBatchRequest:
    count_type = sc.ScalarType(sc.IntType(minimum=0))
    count = sc.coordinate(f"{namespace}_count", count_type)
    program = domain_program(
        "program",
        dialect_id="test.preparation",
        dialect_version="1",
        body=object(),
        inputs={"count": count_type},
        results={
            "raw": ("raw", "v1"),
        },
    )

    authored_call = domain_call(
        program,
        inputs={"count": count},
        products={
            "raw": ModuleProductDecl(
                "raw",
                value_spec=ProductValueSpec(unit=unit, dtype=dtype),
            )
        },
    )

    @sc.experiment(
        id=f"test.sdk.preparation.{namespace}",
        kind="domain_preparation",
    )
    def selected(experiment: sc.ExperimentContext) -> None:
        results = experiment.use(authored_call)
        experiment.grid(sc.axis(count, (1, 3)))
        experiment.alias(
            results.raw,
            record_id="raw-first" if shared_product_uses else "raw",
        )
        if shared_product_uses:
            experiment.alias(
                results.raw,
                record_id="raw-second",
            )

    resolved = bind_invocation(
        selected.bind(),
        config_profile=load_config(),
    )
    bound = resolved
    bound_points = prepare_bound_points(bound)
    execution = bound.program.program.domain_executions[0]
    execution_id = execution.id
    product_use_ids = domain_result_product_use_ids(bound.bindings, execution)
    call_view = make_domain_call_view(
        bound,
        execution_id,
        product_use_ids,
    )
    return make_domain_batch_request(
        call_view,
        bound_points,
        (0, 1),
        legal_cut_offsets=(1, 2),
        batch_ordinal=0,
    )


def _valid_mapping_inputs(
    context: DomainBatchRequest,
) -> tuple[_ResultBinding, ...]:
    return tuple(
        DomainResultBinding(
            f"result-{point.ordinal}",
            point,
            product_use,
        )
        for point in context.points
        for product_use in context.product_uses
    )


def test_point_candidate_reuses_prepared_buffers_across_subranges(
    tmp_path: Path,
) -> None:
    request = _preparation_context(tmp_path, namespace="retained-points")
    buffers = [bytearray(b"first"), bytearray(b"second")]
    seen: list[tuple[DomainBatchRequest, tuple[bytearray, ...]]] = []

    class CompilationReached(Exception):
        pass

    def compile_batch(
        selected: DomainBatchRequest, prepared: tuple[bytearray, ...]
    ) -> PreparedDomainExecution:
        seen.append((selected, prepared))
        raise CompilationReached

    candidate = DomainBatchCandidate.from_points(
        request,
        buffers,
        retained_bytes=sum(map(len, buffers)),
        compile_batch=compile_batch,
    )
    assert candidate.compatible_point_count == 2
    assert candidate.preparation_cost.analyzed_point_count == 2
    assert candidate.preparation_cost.retained_bytes == 11
    assert seen == []

    # Host splitting can start after the first prepared point and reuse a prefix.
    for index in (1, 0, 1):
        selected = replace(
            request,
            points=(request.points[index],),
            parameters=(request.parameters[index],),
            legal_cut_offsets=(1,),
            inputs=DomainBatchInputs(
                program=tuple(
                    (name, (values[index],)) for name, values in request.inputs.program
                ),
                compiler=(),
            ),
        )
        with pytest.raises(CompilationReached):
            candidate.compile(selected)
        actual_request, (actual_buffer,) = seen[-1]
        assert actual_request is selected
        assert actual_buffer is buffers[index]

    with pytest.raises(ValueError, match="zip"):
        DomainBatchCandidate.from_points(
            request,
            buffers[:1],
            retained_bytes=5,
            compile_batch=compile_batch,
        )


def test_input_evidence_must_cover_selected_batch_and_cannot_be_overwritten(
    tmp_path: Path,
) -> None:
    context = _preparation_context(tmp_path, namespace="input-evidence")
    assert context.inputs.parameter_reads is not None
    partial = replace(
        context,
        inputs=replace(
            context.inputs, parameter_reads=context.inputs.parameter_reads[:1]
        ),
    )
    with pytest.raises(ValueError, match="exactly the selected"):
        attach_domain_input_reads(partial, {})
    attached = attach_domain_input_reads(context, {"profile": "test"})
    with pytest.raises(ValueError, match="already contains"):
        attach_domain_input_reads(context, attached)
    unavailable = replace(context, inputs=replace(context.inputs, parameter_reads=None))
    assert attach_domain_input_reads(unavailable, {"profile": "test"}) == {
        "profile": "test"
    }


def test_map_measurements_closes_exact_product_cover(
    tmp_path: Path,
) -> None:
    context = _preparation_context(tmp_path, namespace="direct")
    preparation = DomainPreparationBuilder(context)
    results = _valid_mapping_inputs(context)

    mapping = preparation.map_measurements(results=results)

    assert isinstance(mapping, DomainResultMapping)
    assert mapping.context is context
    assert len(context.product_uses) == 1
    assert tuple(result.point for result in mapping.results) == context.points
    assert tuple(result.result_address for result in mapping.results) == (
        "result-0",
        "result-1",
    )
    for result, point in zip(mapping.results, context.points, strict=True):
        assert result.point is point
        assert result.product_uses == context.product_uses
        assert all(
            actual is expected
            for actual, expected in zip(
                result.product_uses,
                context.product_uses,
                strict=True,
            )
        )


def test_result_values_project_directly_to_canonical_candidates(tmp_path: Path) -> None:
    context = _preparation_context(tmp_path, namespace="values")
    results = _valid_mapping_inputs(context)
    mapping = DomainPreparationBuilder(context).map_measurements(results=results)
    values = tuple(
        DomainOutputValue(
            result.result_address,
            MeasurementScalar.create(dtype="int64", value=index, unit="count"),
        )
        for index, result in enumerate(mapping.results)
    )

    candidates: list[MeasurementValueCandidate] = []
    stream_domain_output_values(
        mapping,
        reversed(values),
        accept=candidates.append,
    )

    assert tuple(
        (candidate.logical_point_id, candidate.product_use_id)
        for candidate in candidates
    ) == tuple(
        (result.logical_point_id, use_id)
        for result in mapping.results
        for use_id in result.product_use_ids
    )
    with pytest.raises(ProviderContractError) as caught:
        stream_domain_output_values(
            mapping,
            values[:-1],
            accept=candidates.append,
        )
    assert {problem.code for problem in caught.value.problems} == {
        "domain_output_missing_result"
    }
    assert len(candidates) == 2


@pytest.mark.parametrize(
    ("dtype", "value"),
    (("bool", True), ("string", "ready")),
)
def test_result_values_accept_every_scalar_dtype(
    tmp_path: Path,
    dtype: MeasurementDType,
    value: bool | str,
) -> None:
    context = _preparation_context(
        tmp_path,
        namespace=f"scalar-{dtype}",
        dtype=dtype,
        unit=None,
    )
    mapping = DomainPreparationBuilder(context).map_measurements(
        results=_valid_mapping_inputs(context)
    )
    values = tuple(
        DomainOutputValue(
            result.result_address,
            MeasurementScalar.create(dtype=dtype, value=value),
        )
        for result in mapping.results
    )

    candidates: list[MeasurementValueCandidate] = []
    stream_domain_output_values(mapping, values, accept=candidates.append)

    assert [candidate.value for candidate in candidates] == [
        MeasurementScalar.create(dtype=dtype, value=value),
        MeasurementScalar.create(dtype=dtype, value=value),
    ]


def test_map_measurements_rejects_foreign_point_and_product_use(
    tmp_path: Path,
) -> None:
    context = _preparation_context(tmp_path, namespace="owned")
    foreign = _preparation_context(tmp_path, namespace="foreign")
    preparation = DomainPreparationBuilder(context)
    results = _valid_mapping_inputs(context)

    foreign_point_bindings = (
        DomainResultBinding(
            results[0].result_address,
            foreign.points[0],
            results[0].product_use,
        ),
        *results[1:],
    )
    with pytest.raises(ValueError, match="point outside this batch context"):
        preparation.map_measurements(results=foreign_point_bindings)

    foreign_results = (
        DomainResultBinding(
            results[0].result_address,
            results[0].point,
            foreign.product_uses[0],
        ),
        *results[1:],
    )
    with pytest.raises(ValueError, match="foreign product use"):
        preparation.map_measurements(results=foreign_results)


def test_map_measurements_rejects_missing_and_duplicate_logical_output(
    tmp_path: Path,
) -> None:
    context = _preparation_context(tmp_path, namespace="exact-cover")
    preparation = DomainPreparationBuilder(context)
    results = _valid_mapping_inputs(context)

    with pytest.raises(ValueError, match="exactly cover every logical"):
        preparation.map_measurements(results=results[:-1])

    with pytest.raises(ValueError, match="unique point/product-use outputs"):
        preparation.map_measurements(results=(*results, results[0]))


def test_map_measurements_fans_one_physical_result_out_to_two_uses_of_product(
    tmp_path: Path,
) -> None:
    context = _preparation_context(
        tmp_path,
        namespace="fanout",
        shared_product_uses=True,
    )
    preparation = DomainPreparationBuilder(context)
    results = _valid_mapping_inputs(context)

    assert len(context.product_uses) == 2
    assert context.product_uses[0].id != context.product_uses[1].id
    assert context.product_uses[0].product is context.product_uses[1].product
    assert len(results) == 2 * len(context.points)
    for point in context.points:
        selected = tuple(binding for binding in results if binding.point is point)
        assert len(selected) == 2
        assert selected[0].result_address == selected[1].result_address

    split_results = tuple(
        DomainResultBinding(
            f"result-{binding.point.ordinal}-{index}",
            binding.point,
            binding.product_use,
        )
        for index, binding in enumerate(results)
    )
    with pytest.raises(ValueError, match="cannot be split across locations"):
        preparation.map_measurements(results=split_results)

    incomplete_results = tuple(
        binding for binding in results if binding.product_use is context.product_uses[0]
    )
    with pytest.raises(ValueError, match="exactly cover every logical"):
        preparation.map_measurements(results=incomplete_results)

    mapping = preparation.map_measurements(results=results)
    assert mapping.context is context
    for result, point in zip(mapping.results, context.points, strict=True):
        assert result.point is point
        assert all(
            actual is expected
            for actual, expected in zip(
                result.product_uses,
                context.product_uses,
                strict=True,
            )
        )


def test_measurement_plan_and_build_close_the_complete_public_sdk_declaration(
    tmp_path: Path,
) -> None:
    context = _preparation_context(tmp_path, namespace="complete-sdk")
    preparation = DomainPreparationBuilder(context)
    results = _valid_mapping_inputs(context)
    mapping = preparation.map_measurements(results=results)

    invocation = DomainInvocationSpec(
        invocation_id="test.complete-sdk.invocation",
        target_id="test.target",
        compiler_id="test.compiler",
        capability_fingerprint="test.interfaces.v1",
        artifact_id="test.artifact",
        artifact_fingerprint="test.artifact.v1",
        execution_summary={"instruments": ["instrument-a", "instrument-b"]},
        target_intent={"mode": "test"},
        payload={"job": "test"},
    )

    def reject_realization(
        executed: DomainExecutionResult[dict[str, str]],
    ) -> tuple[DomainResultValue[str], ...]:
        del executed
        raise AssertionError("preparation must not realize")

    guard_enabled = DomainStateAddress(
        instrument_id="guard-instrument",
        interface_id="test.guard/v1",
        property_id="enabled",
    )
    resident_program = DomainResidencyRequirement(
        address=DomainResidencyAddress(
            instrument_id="instrument-b",
            slot_id="program",
        ),
        content_fingerprint="program-v1",
    )
    prepared = preparation.build(
        instrument_ids=("instrument-b", "instrument-a"),
        setup=_NoEffectsSetup(),
        setup_write_footprint=(
            DomainStateAddress(
                instrument_id="instrument-b",
                interface_id="test.program/v1",
                property_id="loaded",
            ),
        ),
        setup_residency_requirements=(resident_program, resident_program),
        state_requirements=(
            DomainStateRequirement(
                address=guard_enabled,
                value=StateValue(True),
            ),
            DomainStateRequirement(
                address=guard_enabled,
                value=StateValue(True),
            ),
        ),
        realtime_write_footprint=(
            DomainStateAddress(
                instrument_id="instrument-b",
                interface_id="test.output/v1",
                component_path=("channels", "2"),
                property_id="enabled",
            ),
            DomainStateAddress(
                instrument_id="instrument-a",
                interface_id="test.clock/v1",
                property_id="source",
            ),
        ),
        realtime_state_invalidations=(
            DomainStateAddress(
                instrument_id="guard-instrument",
                interface_id="test.guard/v1",
                property_id="latched",
            ),
        ),
        next_batch_max_points=32,
        mapping=mapping,
        invocation=invocation,
        job_runtime=_NoEffectsRuntime(),
        realize=reject_realization,
    )

    assert isinstance(prepared, PreparedDomainExecution)
    assert prepared.instrument_ids == ("instrument-a", "instrument-b")
    assert prepared.invocation.intent.target_intent["mode"] == "test"
    retained = read_domain_input_reads(prepared.invocation.intent)
    assert retained.entries == context.inputs.parameter_reads
    assert [
        (entry.point_ordinal, entry.input_kind, entry.input_id)
        for entry in retained.entries
    ] == [(0, "program", "count"), (1, "program", "count")]
    assert [entry.phase for entry in retained.binding] == ["frontend", "specialization"]
    assert retained.incomplete_reasons == ("binding_structure_not_captured",)
    assert prepared.state_requirements == (
        DomainStateRequirement(
            address=guard_enabled,
            value=StateValue(True),
        ),
    )
    assert prepared.setup_write_footprint == (
        DomainStateAddress(
            instrument_id="instrument-b",
            interface_id="test.program/v1",
            property_id="loaded",
        ),
    )
    assert prepared.setup_residency_requirements == (resident_program,)
    assert prepared.realtime_write_footprint == (
        DomainStateAddress(
            instrument_id="instrument-a",
            interface_id="test.clock/v1",
            property_id="source",
        ),
        DomainStateAddress(
            instrument_id="instrument-b",
            interface_id="test.output/v1",
            component_path=("channels", "2"),
            property_id="enabled",
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            r"domain state requirements conflict for "
            r"guard-instrument:test\.guard/v1\.enabled"
        ),
    ):
        preparation.build(
            instrument_ids=(),
            state_requirements=(
                DomainStateRequirement(
                    address=guard_enabled,
                    value=StateValue(True),
                ),
                DomainStateRequirement(
                    address=guard_enabled,
                    value=StateValue(False),
                ),
            ),
            realtime_write_footprint=(),
            realtime_state_invalidations=(),
            next_batch_max_points=32,
            mapping=mapping,
            invocation=invocation,
            job_runtime=_NoEffectsRuntime(),
            realize=reject_realization,
        )
    with pytest.raises(
        ValueError,
        match="domain residency requirements conflict for instrument-b:program",
    ):
        preparation.build(
            instrument_ids=("instrument-b",),
            setup=_NoEffectsSetup(),
            setup_residency_requirements=(
                resident_program,
                DomainResidencyRequirement(
                    address=resident_program.address,
                    content_fingerprint="program-v2",
                ),
            ),
            state_requirements=(),
            realtime_write_footprint=(),
            realtime_state_invalidations=(),
            next_batch_max_points=32,
            mapping=mapping,
            invocation=invocation,
            job_runtime=_NoEffectsRuntime(),
            realize=reject_realization,
        )
    assert prepared.realtime_state_invalidations == (
        DomainStateAddress(
            instrument_id="guard-instrument",
            interface_id="test.guard/v1",
            property_id="latched",
        ),
    )
