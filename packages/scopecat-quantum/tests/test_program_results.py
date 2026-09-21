from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from scopecat import Quantity
from scopecat.compiler.bind import BoundPlan, _bind_program_facts
from scopecat.compiler.bound_facts import BoundProgramFacts, record_product
from scopecat.compiler.environment import ConfigEnvironment
from scopecat.compiler.frontend.logical_verification import VerifiedLogicalProgram
from scopecat.compiler.point_domain import PointDomain
from scopecat.config.documents import load_config_snapshot_document
from scopecat.config.environment import build_config_environment
from scopecat.domain.program import DomainProgramDef, DomainResultPort
from scopecat.kernel.product_identity import product_id
from scopecat.kernel.value_types import Float, Scalar
from scopecat.measurements.products import ProductAxisDef, ProductDef
from scopecat.planning.domain_bridge import (
    make_domain_batch_request,
    make_domain_call_view,
)
from scopecat.planning.domain_results import domain_result_product_use_ids
from scopecat.planning.point_materialization import prepare_bound_points
from scopecat.program.logical import LogicalDomainExecution, LogicalProgram
from scopecat.program.point_domain import point_axis_values
from scopecat.sdk.domain import (
    DomainPreparationBuilder,
    DomainResultMapping,
)

from scopecat_quantum._ids import (
    AcquisitionSlotId,
    CircuitOperationId,
    PulseEventId,
    PulseProgramId,
    QuantumProgramId,
    QubitId,
    TargetArtifactId,
    TargetCompileEntryId,
    TargetCompilerId,
    TargetId,
)
from scopecat_quantum.acquisitions import INTEGRATED_IQ_RESULT
from scopecat_quantum.program_results import (
    MappedQuantumTarget,
    QuantumTargetEntryPointBinding,
    QuantumTargetResultAddress,
    QuantumTargetResultUseBinding,
    map_quantum_target_results,
    seal_quantum_target_result_mapping,
)
from scopecat_quantum.program_targets import (
    PreparedQuantumTargetBatch,
    prepare_quantum_target_batch,
    prepare_quantum_target_entry,
)
from scopecat_quantum.programs import (
    PulseBlock,
    QuantumProgramIR,
    plan_quantum_pulse_lowering,
    verify_quantum_program,
)
from scopecat_quantum.pulse_implementations import ResolvedPulseImplementations
from scopecat_quantum.pulses import (
    Acquire,
    AcquireSignal,
    AcquisitionSlot,
    Constant,
    Play,
    PulseProgram,
    ReadoutSignal,
)
from scopecat_quantum.pulses import Parallel as PulseParallel
from scopecat_quantum.targets import (
    TargetAcquisitionAddress,
    TargetCompileRequest,
)


def bind_program_facts(
    bindings: BoundProgramFacts,
    execution: LogicalDomainExecution,
    environment: ConfigEnvironment,
    *,
    experiment_id: str,
) -> BoundPlan:
    program = VerifiedLogicalProgram(
        program=LogicalProgram(
            experiment_id=experiment_id,
            kind="quantum_test",
            effects=(execution,),
        ),
        product_declarations={},
        scalar_values={},
    )
    return _bind_program_facts(program, bindings, environment)


Q0 = QubitId("q0")
_REPO_ROOT = Path(__file__).parents[3]


def _preparation(
    *,
    program_id: str = "mixed-program-result-mapping",
    product: ProductDef | None = None,
) -> DomainPreparationBuilder:
    point_domain = PointDomain(
        axes=(
            point_axis_values(
                "coordinate",
                Scalar(Float()),
                (10.0, 20.0),
            ),
        )
    )
    selected_product = (
        ProductDef(
            id=product_id("result"),
            dtype="complex128",
            unit="ratio",
            axes=(
                ProductAxisDef(
                    id="shot",
                    dimension_id="shot",
                    kind="shot",
                    size=11,
                    dimension_label="shot",
                    unit="count",
                ),
            ),
            metadata={"quantum.acquisition_kind": "integrated_iq"},
        )
        if product is None
        else product
    )
    product_use, record_use = record_product(selected_product, record_id="record")
    execution = LogicalDomainExecution(
        id="domain",
        program=DomainProgramDef(
            id="program",
            dialect_id="test.quantum.mixed-result-mapping",
            dialect_version="1",
            body=object(),
            result_ports=(DomainResultPort("result", INTEGRATED_IQ_RESULT),),
        ),
        results=(("result", selected_product.id),),
    )
    program = BoundProgramFacts(
        point_domain=point_domain,
        product_defs=(selected_product,),
        domain_result_use_ids={(execution.id, "result"): (product_use.id,)},
        product_uses=(product_use,),
        record_uses=(record_use,),
    )
    environment = build_config_environment(
        load_config_snapshot_document(
            _REPO_ROOT / "fixtures" / "core" / "simple_scan" / "config-snapshot.json"
        )
    )
    bound_points = prepare_bound_points(
        bind_program_facts(
            program,
            execution,
            environment,
            experiment_id=program_id,
        )
    )
    product_use_ids = domain_result_product_use_ids(
        bound_points.bound_plan.bindings,
        execution,
    )
    point_ordinals = (0, 1)
    call = make_domain_call_view(
        bound_points.bound_plan,
        "domain",
        product_use_ids,
    )
    request = make_domain_batch_request(
        call,
        bound_points,
        point_ordinals,
        legal_cut_offsets=(1, 2),
        batch_ordinal=0,
    )
    return DomainPreparationBuilder(request)


def _prepared(
    entry_id: str, source_program_id: str, *, result_id: str = "template-result"
):
    slot = AcquisitionSlot(
        id=AcquisitionSlotId(result_id),
        contract=INTEGRATED_IQ_RESULT,
        signal=AcquireSignal(Q0),
    )
    duration = Quantity(4, "ns")
    template = PulseProgram(
        id=PulseProgramId(f"{source_program_id}-template"),
        body=PulseParallel(
            (
                Play(
                    id=PulseEventId("readout"),
                    signal=ReadoutSignal(Q0),
                    envelope=Constant(
                        duration=duration,
                        amplitude=Quantity(0.2, "arb"),
                    ),
                ),
                Acquire(
                    id=PulseEventId("acquire"),
                    signal=slot.signal,
                    slot_id=slot.id,
                    duration=duration,
                ),
            )
        ),
        acquisition_slots=(slot,),
    )
    source = QuantumProgramIR(
        id=QuantumProgramId(source_program_id),
        body=PulseBlock(
            id=CircuitOperationId("inline-readout"),
            pulse_template=template,
        ),
    )
    plan = plan_quantum_pulse_lowering(
        verify_quantum_program(source, ()),
        ResolvedPulseImplementations(),
        output_id=PulseProgramId(f"{source_program_id}-pulses"),
    )
    return prepare_quantum_target_entry(TargetCompileEntryId(entry_id), plan)


def _batch() -> PreparedQuantumTargetBatch:
    entries = (
        _prepared("entry-b", "mixed-source-b"),
        _prepared("entry-a", "mixed-source-a"),
    )
    return prepare_quantum_target_batch(
        entries,
        repetitions=11,
    )


def _valid_inputs(product: ProductDef | None = None):
    preparation = _preparation(product=product)
    batch = _batch()
    points = preparation.context.points
    product_use = preparation.context.product_uses[0]
    entry_bindings = (
        QuantumTargetEntryPointBinding(batch.entries[0].id, points[1]),
        QuantumTargetEntryPointBinding(batch.entries[1].id, points[0]),
    )
    result_bindings = tuple(
        QuantumTargetResultUseBinding(
            QuantumTargetResultAddress((address,)),
            product_use,
        )
        for address in batch.acquisition_addresses
    )
    return preparation, batch, entry_bindings, result_bindings


@dataclass(frozen=True, slots=True)
class _TargetArtifact:
    id: TargetArtifactId
    target_id: TargetId
    compiler_id: TargetCompilerId
    capability_fingerprint: str
    artifact_fingerprint: str
    source_entry_ids: tuple[TargetCompileEntryId, ...]
    repetitions: int


@dataclass(frozen=True, slots=True)
class _TargetCompiler:
    id: TargetCompilerId
    target_id: TargetId
    capability_fingerprint: str

    def compile(self, request: TargetCompileRequest) -> _TargetArtifact:
        return _TargetArtifact(
            id=TargetArtifactId("artifact"),
            target_id=self.target_id,
            compiler_id=self.id,
            capability_fingerprint=self.capability_fingerprint,
            artifact_fingerprint="artifact-content:v1",
            source_entry_ids=tuple(entry.id for entry in request.entries),
            repetitions=request.repetitions,
        )


def _compile(
    request: TargetCompileRequest,
) -> _TargetArtifact:
    compiler = _TargetCompiler(
        id=TargetCompilerId("compiler.v1"),
        target_id=TargetId("target"),
        capability_fingerprint="capabilities:v1",
    )
    return compiler.compile(request)


def test_named_mapping_preserves_explicit_point_assignment() -> None:
    preparation = _preparation()
    batch = prepare_quantum_target_batch(
        tuple(
            _prepared(entry_id, entry_id, result_id="result")
            for entry_id in ("entry-b", "entry-a")
        ),
        repetitions=11,
    )
    points = preparation.context.points
    mapping = map_quantum_target_results(
        preparation,
        batch,
        (
            QuantumTargetEntryPointBinding(batch.entries[0].id, points[1]),
            QuantumTargetEntryPointBinding(batch.entries[1].id, points[0]),
        ),
    )
    assert tuple(result.result_address.entry_id for result in mapping.results) == (
        batch.entries[1].id,
        batch.entries[0].id,
    )
    assert tuple(result.point for result in mapping.results) == points
    assert {
        address
        for result in mapping.results
        for address in result.result_address.acquisitions
    } == set(batch.acquisition_addresses)


def test_named_mapping_rejects_unmatched_logical_result() -> None:
    preparation, batch, entry_bindings, _ = _valid_inputs()
    with pytest.raises(ValueError, match="require acquisitions"):
        map_quantum_target_results(preparation, batch, entry_bindings)


def test_mapping_preserves_logical_order_and_acquisition_addresses() -> None:
    preparation, batch, entry_bindings, result_bindings = _valid_inputs()

    mapping = seal_quantum_target_result_mapping(
        preparation,
        batch,
        entry_bindings,
        result_bindings,
    )

    assert isinstance(mapping, DomainResultMapping)
    assert mapping.context is preparation.context
    assert tuple(result.result_address.entry_id for result in mapping.results) == (
        batch.entries[1].id,
        batch.entries[0].id,
    )
    assert {
        address
        for result in mapping.results
        for address in result.result_address.acquisitions
    } == set(batch.acquisition_addresses)


@pytest.mark.parametrize("change", ("dtype", "unit", "local_dimension"))
def test_mapping_closes_the_complete_quantum_result_contract(change: str) -> None:
    product = ProductDef(
        id=product_id("result"),
        dtype="complex128",
        unit="ratio",
        axes=(
            ProductAxisDef(
                id="shot",
                dimension_id="shot",
                kind="shot",
                size=11,
                dimension_label="shot",
                unit="count",
            ),
        ),
        metadata={"quantum.acquisition_kind": "integrated_iq"},
    )
    if change == "dtype":
        product = replace(product, dtype="float64")
    elif change == "unit":
        product = replace(product, unit="V")
    else:
        product = replace(
            product,
            axes=(
                *product.axes,
                ProductAxisDef(
                    id="capture",
                    dimension_id="capture",
                    kind="capture",
                    size=2,
                ),
            ),
        )
    preparation, batch, entry_bindings, result_bindings = _valid_inputs(product)

    with pytest.raises(ValueError, match=r"dtype/unit|local axes"):
        seal_quantum_target_result_mapping(
            preparation,
            batch,
            entry_bindings,
            result_bindings,
        )


@pytest.mark.parametrize("change", ("missing", "duplicate", "foreign"))
def test_entry_mapping_requires_exact_coverage(change: str) -> None:
    preparation, batch, entry_bindings, result_bindings = _valid_inputs()
    selected = entry_bindings
    if change == "missing":
        selected = selected[:-1]
    elif change == "duplicate":
        selected = (selected[0], selected[0])
    else:
        selected = (
            selected[0],
            replace(selected[1], entry_id=TargetCompileEntryId("foreign")),
        )

    with pytest.raises(ValueError, match="entry-point bindings"):
        seal_quantum_target_result_mapping(
            preparation,
            batch,
            selected,
            result_bindings,
        )


@pytest.mark.parametrize("change", ("missing", "duplicate", "foreign"))
def test_result_mapping_requires_exact_qualified_addresses(change: str) -> None:
    preparation, batch, entry_bindings, result_bindings = _valid_inputs()
    selected = result_bindings
    if change == "missing":
        selected = selected[:-1]
    elif change == "duplicate":
        selected = (selected[0], selected[0])
    else:
        selected = (
            selected[0],
            replace(
                selected[1],
                address=QuantumTargetResultAddress(
                    (
                        TargetAcquisitionAddress(
                            entry_id=batch.entries[1].id,
                            slot_id=AcquisitionSlotId("foreign"),
                        ),
                    )
                ),
            ),
        )

    with pytest.raises(
        ValueError,
        match=(
            r"logical output|point/product-use outputs|"
            r"prepared acquisition addresses"
        ),
    ):
        seal_quantum_target_result_mapping(
            preparation,
            batch,
            entry_bindings,
            selected,
        )


def test_mapped_target_retains_only_artifact_and_logical_mapping() -> None:
    preparation, batch, entry_bindings, result_bindings = _valid_inputs()
    mapping = seal_quantum_target_result_mapping(
        preparation,
        batch,
        entry_bindings,
        result_bindings,
    )
    artifact = _compile(batch.request)

    mapped = MappedQuantumTarget(artifact, mapping)

    assert mapped.artifact is artifact
    assert mapped.mapping is mapping
