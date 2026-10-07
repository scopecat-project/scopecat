from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Annotated, cast

import pytest
import scopecat as sc
from scopecat import Quantity
from scopecat.compiler.bind import bind_program
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.config.documents import load_config_snapshot_document
from scopecat.config.environment import build_config_environment
from scopecat.measurements.records import plan_records
from scopecat.planning.domain_bridge import (
    make_domain_batch_request,
    make_domain_call_view,
)
from scopecat.planning.domain_results import domain_result_product_use_ids
from scopecat.planning.point_materialization import prepare_bound_points
from scopecat.sdk.domain import DomainPreparationBuilder, DomainResultBinding

from scopecat_quantum import authoring
from scopecat_quantum._ids import (
    PulseEventId,
    PulseProgramId,
    QubitId,
    TargetCompileEntryId,
)
from scopecat_quantum.acquisitions import AcquisitionKind
from scopecat_quantum.circuits import Measure
from scopecat_quantum.program_results import (
    QuantumTargetEntryPointBinding,
    QuantumTargetResultAddress,
    QuantumTargetResultUseBinding,
    map_quantum_target_results,
    seal_quantum_target_result_mapping,
)
from scopecat_quantum.program_targets import (
    prepare_quantum_target_batch,
    prepare_quantum_target_entry,
)
from scopecat_quantum.programs import plan_quantum_pulse_lowering
from scopecat_quantum.pulse_implementations import ResolvedPulseImplementations
from scopecat_quantum.pulses import Acquire, PulseProgram, ScheduledPulseProgram
from scopecat_quantum.pulses import Sequence as PulseSequence
from scopecat_quantum.realtime import TargetProgram
from scopecat_quantum.targets import TargetAcquisitionAddress

_REPO_ROOT = Path(__file__).parents[3]


def _config_with_two_qubits():
    config = load_config_snapshot_document(
        _REPO_ROOT / "fixtures" / "core" / "simple_scan" / "config-snapshot.json"
    )
    topology = config.system.topology.model_copy(
        update={
            "entities": [
                sc.EntityRef(id="q0", kind="logical_qubit"),
                sc.EntityRef(id="q1", kind="logical_qubit"),
                *config.system.topology.entities[1:],
            ]
        }
    )
    return config.model_copy(
        update={"system": config.system.model_copy(update={"topology": topology})}
    )


def test_result_contract_validates_canonical_acquisition_shapes() -> None:
    assert authoring.CLASSIFIED_STATE_RESULT.acquisition_kind is (
        AcquisitionKind.CLASSIFIED_STATE
    )
    assert authoring.CLASSIFIED_STATE_RESULT.dtype == "int64"
    assert authoring.CLASSIFIED_STATE_RESULT.unit is None

    with pytest.raises(ValueError, match="exactly one sample dimension"):
        authoring.QuantumResultContract(
            acquisition_kind=AcquisitionKind.RAW_TRACE,
            dtype="complex128",
            unit="ratio",
        )
    with pytest.raises(ValueError, match="sample dimensions are reserved"):
        authoring.QuantumResultContract(
            acquisition_kind=AcquisitionKind.INTEGRATED_IQ,
            dtype="complex128",
            unit="ratio",
            dimensions=(authoring.QuantumResultDimension("sample", "sample", 64),),
        )
    with pytest.raises(ValueError, match="classified-state"):
        authoring.QuantumResultContract(
            acquisition_kind=AcquisitionKind.CLASSIFIED_STATE,
            dtype="bool",
            unit=None,
        )
    with pytest.raises(TypeError, match="positive integer"):
        authoring.QuantumResultDimension("round", "round", cast("int", 1.5))

    unbounded = authoring.input(
        "unbounded",
        sc.ScalarType(sc.IntType(minimum=1)),
    )
    with pytest.raises(ValueError, match="finite maximum"):
        authoring.QuantumResultDimension("round", "round", unbounded)


def test_bounded_input_dimension_is_visible_ragged_and_point_bound() -> None:
    @authoring.program(id="test.quantum.dynamic-rounds")
    def declaration(
        qubit: authoring.Qubit,
        rounds: Annotated[int, sc.IntType(minimum=1, maximum=8)],
    ) -> authoring.QuantumFragment:
        contract = authoring.CLASSIFIED_STATE_RESULT.with_dimensions(
            authoring.QuantumResultDimension("round", "round", rounds)
        )
        return authoring.measure(qubit, result="state", contract=contract)

    assert "round=$rounds(max=8)" in declaration.describe()
    assert "dimensions=round=$rounds(max=8)" in declaration.draw()

    round_coordinate = sc.coordinate(
        "selected-rounds",
        sc.IntType(minimum=1, maximum=8),
    )
    call = declaration("q0", round_coordinate)
    [product] = call.domain_call.product_declarations
    assert [axis.kind for axis in product.axes] == ["shot", "round"]
    assert [axis.size for axis in product.axes] == [1, None]
    [dimension_metadata] = cast(
        "tuple[Mapping[str, object], ...]",
        product.metadata["quantum.local_dimensions"],
    )
    assert dimension_metadata == {
        "id": "round",
        "kind": "round",
        "unit": None,
        "maximum_size": 8,
        "size_input_id": "rounds",
    }

    bound = authoring.bind(declaration, {"qubit": "q0", "rounds": 5})
    [measurement] = bound.verified.operations
    assert isinstance(measurement, Measure)
    assert measurement.contract.dimensions == (
        authoring.QuantumResultDimension("round", "round", 5),
    )


def test_classified_state_preserves_integer_labels_and_bounded_rounds() -> None:
    rounds = authoring.QuantumResultDimension("round", "round", 5)
    contract = authoring.CLASSIFIED_STATE_RESULT.with_dimensions(rounds)
    assert authoring.CLASSIFIED_STATE_RESULT.dimensions == ()

    @authoring.program(id="test.quantum.classified-rounds")
    def declaration(qubit: authoring.Qubit) -> authoring.QuantumFragment:
        return authoring.measure(qubit, result="state", contract=contract)

    call = declaration("q0").with_shots(16)
    [product] = call.domain_call.product_declarations
    assert product.dtype == "int64"
    assert product.unit is None
    assert product.metadata["quantum.acquisition_kind"] == "classified_state"
    assert [axis.kind for axis in product.axes] == ["shot", "round"]
    assert [axis.size for axis in product.axes] == [16, 5]

    bound = authoring.bind(declaration, {"qubit": "q0"})
    [measurement] = bound.verified.operations
    assert isinstance(measurement, Measure)
    assert measurement.contract == contract


@pytest.mark.parametrize("entity_order", (("q0", "q1"), ("q1", "q0")))
def test_bounded_result_dimensions_cross_authoring_target_and_result_mapping(
    entity_order: tuple[str, str],
) -> None:
    dimensions = (
        authoring.QuantumResultDimension("capture", "capture", 2),
        authoring.QuantumResultDimension("round", "round", 3),
        authoring.QuantumResultDimension("cycle", "cycle", 4),
    )
    contract = authoring.raw_trace_result(64, dimensions=dimensions)

    @authoring.program(id="test.quantum.bounded-local-results")
    def declaration(qubits: authoring.QubitSet) -> authoring.QuantumFragment:
        return authoring.parallel_each(
            qubits,
            lambda qubit: authoring.acquire(
                qubit,
                duration=Quantity(16, "ns"),
                result="trace",
                contract=contract,
            ),
        )

    call = declaration(entity_order).with_shots(8)
    [declared_product] = call.domain_call.product_declarations
    assert declared_product.dtype == "complex128"
    assert declared_product.metadata["quantum.acquisition_kind"] == "raw_trace"
    assert [axis.kind for axis in declared_product.axes] == [
        "entity",
        "shot",
        "capture",
        "round",
        "cycle",
        "sample",
    ]

    @sc.experiment(id="test.quantum.bounded-local-results", kind="quantum")
    def experiment(context: sc.ExperimentContext) -> None:
        context.alias(context.use(call).trace)

    compiled = compile_invocation(experiment.build())
    bound = bind_program(
        compiled.program,
        build_config_environment(_config_with_two_qubits()),
    )
    [product] = bound.bindings.product_defs
    assert [axis.kind for axis in product.axes] == [
        "entity",
        "shot",
        "capture",
        "round",
        "cycle",
        "sample",
    ]
    assert [axis.size for axis in product.axes] == [2, 8, 2, 3, 4, 64]
    assert product.axes[0].entities is not None
    assert [entity.id for entity in product.axes[0].entities] == list(entity_order)
    [record] = plan_records(
        bound.bindings.product_defs,
        bound.bindings.product_uses,
        bound.bindings.product_record_uses,
    )
    assert [axis.kind for axis in record.axes] == [
        "entity",
        "shot",
        "capture",
        "round",
        "cycle",
        "sample",
    ]

    quantum = authoring.bind(declaration, {"qubits": entity_order})
    pulse_plan = plan_quantum_pulse_lowering(
        quantum.verified,
        ResolvedPulseImplementations(),
        output_id=PulseProgramId("bounded-local-results"),
    )
    target_entry = prepare_quantum_target_entry(
        TargetCompileEntryId("point-0"),
        pulse_plan,
    )
    target_batch = prepare_quantum_target_batch((target_entry,), repetitions=8)
    assert len(target_batch.acquisition_addresses) == 2
    assert all(
        slot.contract == contract for slot in target_entry.program.acquisition_slots
    )
    for slot in target_entry.program.acquisition_slots:
        assert slot.contract.acquisition_kind is AcquisitionKind.RAW_TRACE
        assert slot.contract.dtype == "complex128"
        assert slot.contract.unit == "ratio"
        assert slot.contract.dimensions == contract.dimensions

    points = prepare_bound_points(bound)
    [execution] = bound.program.program.domain_executions
    product_use_ids = domain_result_product_use_ids(
        points.bound_plan.bindings,
        execution,
    )
    call_view = make_domain_call_view(
        points.bound_plan,
        execution.id,
        product_use_ids,
    )
    request = make_domain_batch_request(
        call_view,
        points,
        (0,),
        legal_cut_offsets=(1,),
        batch_ordinal=0,
    )
    preparation = DomainPreparationBuilder(request)
    mapping = seal_quantum_target_result_mapping(
        preparation,
        target_batch,
        (
            QuantumTargetEntryPointBinding(
                target_entry.id,
                request.points[0],
            ),
        ),
        (
            QuantumTargetResultUseBinding(
                QuantumTargetResultAddress(
                    tuple(
                        sorted(
                            target_batch.acquisition_addresses,
                            key=lambda address: entity_order.index(
                                address.slot_id.scope[-1]
                            ),
                        )
                    )
                ),
                request.product_uses[0],
            ),
        ),
    )
    [mapped] = mapping.results
    inferred = map_quantum_target_results(
        preparation,
        target_batch,
        (QuantumTargetEntryPointBinding(target_entry.id, request.points[0]),),
    )
    assert inferred.results == mapping.results
    assert mapped.product == product
    assert [axis.kind for axis in mapped.product.axes] == [
        "entity",
        "shot",
        "capture",
        "round",
        "cycle",
        "sample",
    ]


def _grouped_mapping_inputs():
    @authoring.program(id="test.quantum.entity-result-order")
    def declaration(qubits: authoring.QubitSet) -> authoring.QuantumFragment:
        return authoring.parallel_each(
            qubits,
            lambda qubit: authoring.sequence(
                authoring.acquire(qubit, duration=Quantity(4, "ns"), result="first"),
                authoring.acquire(qubit, duration=Quantity(4, "ns"), result="second"),
            ),
        )

    call = declaration(("q1", "q0")).with_shots(3)

    @sc.experiment
    def experiment(context: sc.ExperimentContext) -> None:
        context.scan("index", (10, 20))
        results = context.use(call)
        context.alias(results.first)
        context.alias(results.second)

    bound = bind_program(
        compile_invocation(experiment.build()).program,
        build_config_environment(_config_with_two_qubits()),
    )
    quantum = authoring.bind(declaration, {"qubits": ("q1", "q0")})
    pulse_plan = plan_quantum_pulse_lowering(
        quantum.verified,
        ResolvedPulseImplementations(),
        output_id=PulseProgramId("entity-result-order"),
    )
    batch = prepare_quantum_target_batch(
        tuple(
            prepare_quantum_target_entry(TargetCompileEntryId(entry), pulse_plan)
            for entry in ("entry-b", "entry-a")
        ),
        repetitions=3,
    )
    points = prepare_bound_points(bound)
    [execution] = bound.program.program.domain_executions
    call_view = make_domain_call_view(
        points.bound_plan,
        execution.id,
        domain_result_product_use_ids(points.bound_plan.bindings, execution),
    )
    request = make_domain_batch_request(
        call_view, points, (0, 1), legal_cut_offsets=(1, 2), batch_ordinal=0
    )
    entries = (
        QuantumTargetEntryPointBinding(batch.entries[0].id, request.points[1]),
        QuantumTargetEntryPointBinding(batch.entries[1].id, request.points[0]),
    )
    return DomainPreparationBuilder(request), batch, entries


def test_grouped_mapping_orders_entities_independently_for_each_point_and_product() -> (
    None
):
    preparation, batch, entries = _grouped_mapping_inputs()
    mapping = map_quantum_target_results(preparation, batch, entries)
    assert len(mapping.results) == 4
    assert [result.point for result in mapping.results] == [
        point for point in preparation.context.points for _ in range(2)
    ]
    owners = {
        TargetAcquisitionAddress(entry.id, slot.id): slot.signal.owner.value
        for entry in batch.request.entries
        for slot in entry.program.acquisition_slots
    }
    expected_bindings: list[QuantumTargetResultUseBinding] = []
    target_order_bindings: list[DomainResultBinding[QuantumTargetResultAddress]] = []
    for result in mapping.results:
        entities = result.product.axes[0].entities
        assert entities is not None
        assert tuple(entity.id for entity in entities) == ("q1", "q0")
        addresses = result.result_address.acquisitions
        assert tuple(owners[address] for address in addresses) == ("q1", "q0")
        assert result.result_address.entry_id == next(
            entry.entry_id for entry in entries if entry.point is result.point
        )
        [logical_result] = [
            logical
            for logical in preparation.context.call.results
            if logical.product_uses == result.product_uses
        ]
        assert {address.slot_id.local_id for address in addresses} == {
            logical_result.id
        }
        for use in result.product_uses:
            expected_bindings.append(
                QuantumTargetResultUseBinding(result.result_address, use)
            )
            target_order_bindings.append(
                DomainResultBinding(
                    QuantumTargetResultAddress(tuple(reversed(addresses))),
                    result.point,
                    use,
                )
            )
    explicit = seal_quantum_target_result_mapping(
        preparation, batch, entries, expected_bindings
    )
    assert explicit.results == mapping.results
    assert explicit.contract_fingerprint == mapping.contract_fingerprint
    wrong_order = preparation.map_measurements(results=target_order_bindings)
    assert wrong_order.contract_fingerprint != mapping.contract_fingerprint


@pytest.mark.parametrize(
    "change", ("order", "missing", "duplicate_owner", "foreign_owner")
)
def test_grouped_mapping_rejects_incorrect_entity_correspondence(change: str) -> None:
    preparation, batch, entries = _grouped_mapping_inputs()
    valid = map_quantum_target_results(preparation, batch, entries)
    bindings = tuple(
        QuantumTargetResultUseBinding(result.result_address, use)
        for result in valid.results
        for use in result.product_uses
    )
    expected = "product entity order"
    if change == "order":
        bindings = (
            replace(
                bindings[0],
                address=QuantumTargetResultAddress(
                    tuple(reversed(bindings[0].address.acquisitions))
                ),
            ),
            *bindings[1:],
        )
    elif change == "missing":
        bindings = (
            replace(
                bindings[0],
                address=QuantumTargetResultAddress(
                    bindings[0].address.acquisitions[:1]
                ),
            ),
            *bindings[1:],
        )
        expected = "exactly cover prepared acquisition addresses"
    else:
        # Keep unique acquisition addresses and shape while corrupting one owner:
        # count-only validation cannot detect these malformed target contracts.
        target_entries = list(batch.request.entries)
        entry = target_entries[0]
        slots = list(entry.program.acquisition_slots)
        slot = slots[0]
        slots[0] = replace(
            slot,
            signal=replace(
                slot.signal,
                owner=QubitId("q1" if change == "duplicate_owner" else "foreign"),
            ),
        )
        # A valid sequential target can acquire the same owner twice under
        # different slot names. Mapping must still reject it for this entity axis.
        scheduled = ScheduledPulseProgram(
            PulseProgram(
                id=entry.program.id,
                body=PulseSequence(
                    tuple(
                        Acquire(
                            id=PulseEventId(f"capture-{index}"),
                            signal=selected.signal,
                            slot_id=selected.id,
                            duration=Quantity(4, "ns"),
                        )
                        for index, selected in enumerate(slots)
                    )
                ),
                acquisition_slots=tuple(slots),
            )
        )
        target_entries[0] = replace(
            entry, program=TargetProgram.from_scheduled(scheduled)
        )
        batch = replace(
            batch,
            entries=tuple(
                replace(prepared, target_entry=target)
                for prepared, target in zip(batch.entries, target_entries, strict=True)
            ),
            request=replace(batch.request, entries=tuple(target_entries)),
        )
        expected = (
            "unique qubit owners"
            if change == "duplicate_owner"
            else "exactly cover product entities"
        )
        with pytest.raises(ValueError, match=expected):
            map_quantum_target_results(preparation, batch, entries)
    with pytest.raises(ValueError, match=expected):
        seal_quantum_target_result_mapping(preparation, batch, entries, bindings)
