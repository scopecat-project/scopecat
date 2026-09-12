"""The unified domain compiler for the reference quantum laboratory."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Protocol

import scopecat as sc
from scopecat.inspection import (
    CompiledArtifactInspection,
    CompiledProgramInspectionQuery,
)
from scopecat.kernel.content_identity import content_fingerprint, stable_content_hash
from scopecat.kernel.json_types import JsonValue
from scopecat.sdk.domain import (
    DomainBatchCandidate,
    DomainBatchInputs,
    DomainBatchPreparationCost,
    DomainBatchPreparationLimits,
    DomainBatchRequest,
    DomainCallView,
    DomainExecutionResult,
    DomainPreparationBuilder,
    PreparedDomainExecution,
)
from scopecat_quantum import authoring as quantum
from scopecat_quantum._ids import (
    PulseProgramId,
    TargetCompileEntryId,
    TargetCompilerId,
)
from scopecat_quantum.inspection import build_quantum_program_inspection_snapshot
from scopecat_quantum.program_results import (
    MappedQuantumTarget,
    QuantumTargetEntryPointBinding,
    QuantumTargetResultAddress,
    QuantumTargetResultUseBinding,
    seal_quantum_target_result_mapping,
)
from scopecat_quantum.program_targets import (
    PreparedQuantumTargetBatch,
    PreparedQuantumTargetEntry,
    prepare_quantum_target_batch,
    prepare_quantum_target_entry,
)
from scopecat_quantum.programs import (
    plan_quantum_pulse_lowering,
)
from scopecat_quantum.pulse_implementations import ResolvedPulseImplementations
from scopecat_quantum.pulse_recipes import PulseRecipeMaterializationCache
from scopecat_quantum.realtime import ScheduledBlock
from scopecat_quantum.targets import TargetCompileEntry

from reference_lab.parameters import QubitParameters
from reference_lab.point_values import QuantumLabPointValues
from reference_lab.quantum_compilation.compiler_parameters import (
    QuantumCompilerParameters,
)
from reference_lab.quantum_compilation.pulse_profile import QUANTUM_PULSE_PROFILE
from reference_lab.targets.configuration import (
    LIST_MODE_TARGET_KIND,
)
from reference_lab.targets.list_mode import (
    ConfiguredRoutePlacementProvider,
    ListModeArtifact,
    ListModeCompilationTrace,
    ListModeDomainJobRuntime,
    ListModePlacementProvider,
    ListModeRun,
    ListModeTarget,
    ListModeTargetCompiler,
    MappedListModeTarget,
    build_list_mode_artifact_inspection_snapshot,
    list_mode_measurement_invocation_spec,
    list_mode_realtime_write_footprint,
    list_mode_setup_residency_requirements,
    list_mode_setup_state_invalidations,
    list_mode_state_requirements,
    realize_executed_measurements,
)

_QUANTUM_LAB_TARGET_COMPILER_ID = TargetCompilerId("reference-lab.list-mode-target.v2")
_INITIAL_BATCH_SIZE = 1
_MAX_PREPARATION_RETAINED_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _QuantumLabArtifact:
    program: quantum.Program = field(repr=False)
    points: tuple[QuantumLabPointValues, ...]


@dataclass(frozen=True, slots=True)
class _ListQuantumLabArtifact(_QuantumLabArtifact):
    compiled_points: tuple[_CompiledQuantumPoint, ...]
    entries: tuple[PreparedQuantumTargetEntry, ...]
    batch: PreparedQuantumTargetBatch
    target_artifact: ListModeArtifact = field(repr=False)
    compilation_trace: ListModeCompilationTrace


@dataclass(frozen=True, slots=True)
class _CompiledQuantumPoint:
    values: QuantumLabPointValues
    bound: quantum.BoundProgram = field(repr=False)
    implementations: ResolvedPulseImplementations = field(repr=False)


@dataclass(frozen=True, slots=True)
class QuantumJobRuntimeContext:
    """Compiled logical context available to an optional execution adapter."""

    program: quantum.Program = field(repr=False)
    points: tuple[QuantumLabPointValues, ...]
    entries: tuple[PreparedQuantumTargetEntry, ...]
    repetitions: int


@dataclass(frozen=True, slots=True)
class QuantumJobRuntimeSelection:
    """Job runtime plus stable response intent selected by a lab composition."""

    job_runtime: ListModeDomainJobRuntime
    response_intent: Mapping[str, JsonValue] | None = None


class QuantumJobRuntimeSelector(Protocol):
    """Select execution behavior without changing target compilation."""

    def __call__(
        self,
        context: QuantumJobRuntimeContext,
    ) -> QuantumJobRuntimeSelection: ...


class QuantumLabCompiler:
    """Own the reference lab's single domain-compilation boundary.

    Every bounded request contains the accepted point-local program and
    calibration inputs. Compilation closes the target artifact, result
    mapping, and runtime invocation without reaching into mutable state.
    """

    def __init__(
        self,
        *,
        target: ListModeTarget,
        job_runtime_selector: QuantumJobRuntimeSelector | None = None,
        placement_provider: ListModePlacementProvider | None = None,
    ) -> None:
        self._target = target
        self._job_runtime = ListModeDomainJobRuntime()
        self._job_runtime_selector = job_runtime_selector
        selected_placement_provider = (
            ConfiguredRoutePlacementProvider()
            if placement_provider is None
            else placement_provider
        )
        self._target_compiler = ListModeTargetCompiler(
            _QUANTUM_LAB_TARGET_COMPILER_ID,
            self._target,
            placement_provider=selected_placement_provider,
        )

    @property
    def target_id(self) -> str:
        return self._target.id.value

    @property
    def target_kind(self) -> str:
        return LIST_MODE_TARGET_KIND

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    *(
                        channel.instrument_id
                        for binding in self._target.output_bindings
                        for channel in binding.channel_ids
                    ),
                    *(
                        binding.input_id.instrument_id
                        for binding in self._target.acquisition_bindings
                    ),
                    self._target.preparation.timing.trigger_instrument_id,
                }
            )
        )

    def initial_batch_preparation_limits(
        self,
        point_count: int,
    ) -> DomainBatchPreparationLimits:
        """Bound the first preparation without materializing program inputs."""

        return DomainBatchPreparationLimits(
            max_points=min(
                point_count,
                _INITIAL_BATCH_SIZE,
                self._target.max_list_entries,
            ),
            max_retained_bytes=_MAX_PREPARATION_RETAINED_BYTES,
        )

    def prepare_batch(self, request: DomainBatchRequest) -> DomainBatchCandidate:
        """Lower candidate points once and retain them across host-state splits."""

        program = _quantum_program(request.call)
        _validate_call(request.call, program)
        shots = _shot_count(request.call)
        points = _compile_points(
            program,
            request.inputs,
            request.point_ordinals,
            max_expanded_operations=self._target.max_program_event_count,
        )
        entries = _prepare_target_entries(
            program,
            points,
            max_expanded_operations=self._target.max_program_event_count,
        )
        source_indices = {
            point_ordinal: index
            for index, point_ordinal in enumerate(request.point_ordinals)
        }

        def compile_exact(
            exact_request: DomainBatchRequest,
        ) -> PreparedDomainExecution:
            selected_indices = tuple(
                source_indices[point_ordinal]
                for point_ordinal in exact_request.point_ordinals
            )
            selected_points = tuple(points[index] for index in selected_indices)
            selected_entries = _retarget_target_entries(
                program,
                selected_points,
                tuple(entries[index] for index in selected_indices),
            )
            return self._compile_prepared_batch(
                exact_request,
                program=program,
                points=selected_points,
                entries=selected_entries,
                shots=shots,
            )

        return DomainBatchCandidate(
            compatible_point_count=len(request.points),
            preparation_cost=DomainBatchPreparationCost(
                analyzed_point_count=len(request.points),
                retained_bytes=0,
            ),
            _compile=compile_exact,
        )

    def _compile_target_artifact(
        self,
        program: quantum.Program,
        points: tuple[_CompiledQuantumPoint, ...],
        entries: tuple[PreparedQuantumTargetEntry, ...],
        *,
        shots: int,
    ) -> _ListQuantumLabArtifact:
        batch = prepare_quantum_target_batch(
            entries,
            repetitions=shots,
        )
        target_artifact, compilation_trace = self._target_compiler.compile_with_trace(
            batch.request
        )
        return _ListQuantumLabArtifact(
            program=program,
            points=tuple(point.values for point in points),
            compiled_points=points,
            entries=entries,
            batch=batch,
            target_artifact=target_artifact,
            compilation_trace=compilation_trace,
        )

    def _compile_prepared_batch(
        self,
        request: DomainBatchRequest,
        *,
        program: quantum.Program,
        points: tuple[_CompiledQuantumPoint, ...],
        entries: tuple[PreparedQuantumTargetEntry, ...],
        shots: int,
    ) -> PreparedDomainExecution:
        artifact = self._compile_target_artifact(
            program,
            points,
            entries,
            shots=shots,
        )
        preparation = DomainPreparationBuilder(request)
        entries = artifact.entries
        batch = artifact.batch
        mapping = seal_quantum_target_result_mapping(
            preparation,
            batch,
            tuple(
                QuantumTargetEntryPointBinding(entry.id, point)
                for entry, point in zip(entries, request.points, strict=True)
            ),
            tuple(
                QuantumTargetResultUseBinding(
                    _result_address(entry, result),
                    product_use,
                )
                for entry in entries
                for result in _measurement_results(artifact.program)
                for product_use in request.call.result(result.id).product_uses
            ),
        )
        mapped_target = MappedQuantumTarget(
            artifact.target_artifact,
            mapping,
        )
        selection = self._select_job_runtime(
            QuantumJobRuntimeContext(
                program=artifact.program,
                points=artifact.points,
                entries=entries,
                repetitions=batch.request.repetitions,
            )
        )
        job_runtime = selection.job_runtime
        invocation = list_mode_measurement_invocation_spec(
            mapped_target,
            invocation_id=(
                f"{artifact.program.id}.batch-{request.batch_ordinal}."
                f"point-{artifact.points[0].ordinal}"
            ),
            response_intent=selection.response_intent,
        )

        inspection_snapshot = None
        if request.inspection_requested:
            target_body = artifact.entries[0].program.body
            if not isinstance(target_body, ScheduledBlock):
                raise AssertionError(
                    "reference list-mode compilation accepted real-time control"
                )
            program_inspection_snapshot = build_quantum_program_inspection_snapshot(
                artifact.program,
                bound=artifact.compiled_points[0].bound,
                scheduled=target_body.program,
                snapshot_id=artifact.target_artifact.artifact_fingerprint,
            )
            inspection_snapshot = build_list_mode_artifact_inspection_snapshot(
                artifact.target_artifact,
                program_projector=program_inspection_snapshot.project,
                compilation_trace=artifact.compilation_trace,
            )

        def project_inspection(
            query: CompiledProgramInspectionQuery | None,
        ) -> CompiledArtifactInspection:
            assert inspection_snapshot is not None
            return inspection_snapshot.project(query)

        return preparation.build(
            instrument_ids=artifact.target_artifact.instrument_ids,
            setup=job_runtime,
            setup_residency_requirements=list_mode_setup_residency_requirements(
                artifact.target_artifact
            ),
            setup_state_invalidations=list_mode_setup_state_invalidations(
                artifact.target_artifact
            ),
            state_requirements=list_mode_state_requirements(artifact.target_artifact),
            realtime_write_footprint=list_mode_realtime_write_footprint(
                artifact.target_artifact
            ),
            realtime_state_invalidations=(),
            transition_policy="abnormal_only",
            next_batch_max_points=(
                artifact.target_artifact.compilation_budget.next_batch_max_points
            ),
            inspection=(
                project_inspection(request.inspection_query)
                if request.inspection_requested
                else None
            ),
            inspection_projector=(
                project_inspection if request.inspection_requested else None
            ),
            mapping=mapping,
            invocation=invocation,
            job_runtime=job_runtime,
            realize=lambda fetched: _realize(mapped_target, fetched),
        )

    def _select_job_runtime(
        self,
        context: QuantumJobRuntimeContext,
    ) -> QuantumJobRuntimeSelection:
        if self._job_runtime_selector is None:
            return QuantumJobRuntimeSelection(self._job_runtime)
        return self._job_runtime_selector(context)


def _quantum_program(call: DomainCallView) -> quantum.Program:
    body = call.program.body
    if not (
        call.program.dialect_id == quantum.QUANTUM_PROGRAM_DIALECT_ID
        and call.program.dialect_version == quantum.QUANTUM_PROGRAM_DIALECT_VERSION
        and isinstance(body, quantum.Program)
    ):
        raise ValueError("quantum compiler requires a quantum Program")
    return body


def _validate_call(
    call: DomainCallView,
    program: quantum.Program,
) -> None:
    if tuple(port.id for port in call.program.inputs) != tuple(
        port.id for port in program.ports
    ):
        raise ValueError("quantum Program input ports changed before compilation")
    if tuple(port.id for port in call.program.results) != tuple(
        result.id for result in program.results
    ):
        raise ValueError("quantum Program result ports changed before compilation")
    compiler_input_ids = tuple(port.id for port in call.program.compiler_inputs)
    if compiler_input_ids not in ((), (sc.parameter_table_name(QubitParameters),)):
        raise ValueError("quantum compiler inputs must be the qubits collection")
    for result in program.results:
        binding = call.result(result.id)
        if binding.contract is not result:
            raise ValueError("quantum result contracts must retain authored handles")
    _shot_count(call)


def _shot_count(call: DomainCallView) -> int:
    counts: list[int] = []
    for result in call.results:
        shot_axes = tuple(axis for axis in result.product.axes if axis.kind == "shot")
        if len(shot_axes) != 1:
            raise ValueError("quantum lab result products require one shot axis")
        size = shot_axes[0].size
        if size is None:
            raise ValueError("quantum lab result products require fixed shot counts")
        counts.append(size)
    if not counts or len(set(counts)) != 1:
        raise ValueError("quantum lab result products require one shared shot count")
    return counts[0]


def _compile_points(
    program: quantum.Program,
    inputs: DomainBatchInputs,
    point_ordinals: tuple[int, ...],
    *,
    max_expanded_operations: int,
) -> tuple[_CompiledQuantumPoint, ...]:
    program_inputs = inputs.program
    compiler_parameters = (
        tuple(QuantumCompilerParameters() for _ordinal in point_ordinals)
        if not inputs.compiler
        else inputs.decode_compiler_collection(
            sc.parameter_table_name(QubitParameters),
            QuantumCompilerParameters.from_qubit_rows,
        )
    )
    points = tuple(
        QuantumLabPointValues(
            ordinal=ordinal,
            values=tuple((name, values[index]) for name, values in program_inputs),
        )
        for index, ordinal in enumerate(point_ordinals)
    )
    compiled: list[_CompiledQuantumPoint] = []
    effective_points: dict[
        str,
        tuple[quantum.BoundProgram, ResolvedPulseImplementations],
    ] = {}
    pulse_recipe_cache = PulseRecipeMaterializationCache()
    for point, parameters in zip(points, compiler_parameters, strict=True):
        effective_fingerprint = stable_content_hash(
            content_fingerprint(
                {
                    "schema": "reference_lab.quantum_point_inputs.v1",
                    "program_id": program.id,
                    "values": point.values,
                    "compiler_parameters": parameters,
                }
            )
        )
        retained = effective_points.get(effective_fingerprint)
        if retained is None:
            bound = quantum.bind(program, dict(point.values))
            implementations = QUANTUM_PULSE_PROFILE.materialize_quantum(
                parameters,
                bound.verified,
                max_expanded_operations=max_expanded_operations,
                cache=pulse_recipe_cache,
            )
            effective_points[effective_fingerprint] = (bound, implementations)
        else:
            bound, implementations = retained
        compiled.append(
            _CompiledQuantumPoint(
                values=point,
                bound=bound,
                implementations=implementations,
            )
        )
    return tuple(compiled)


def _prepare_target_entries(
    program: quantum.Program,
    points: tuple[_CompiledQuantumPoint, ...],
    *,
    max_expanded_operations: int,
) -> tuple[PreparedQuantumTargetEntry, ...]:
    """Assign request-local identities from point-effective program content.

    Logical point ordinals belong to result mapping, not physical compilation.
    Reusing content-derived entry identities lets an identical one-entry target
    program share the target compiler's staged caches across host-state points.
    The occurrence suffix keeps duplicate entries unique inside one native batch.
    """

    occurrences: dict[str, int] = {}
    prepared_by_digest: dict[str, PreparedQuantumTargetEntry] = {}
    entries: list[PreparedQuantumTargetEntry] = []
    for point in points:
        digest = _compiled_point_program_digest(program, point)
        occurrence = occurrences.get(digest, 0)
        occurrences[digest] = occurrence + 1
        local_id = f"{program.id}.content-{digest[:16]}.entry-{occurrence}"
        retained = prepared_by_digest.get(digest)
        if retained is None:
            retained = prepare_quantum_target_entry(
                TargetCompileEntryId(local_id),
                plan_quantum_pulse_lowering(
                    point.bound.verified,
                    point.implementations,
                    output_id=PulseProgramId(
                        f"{program.id}.content-{digest[:16]}.pulses"
                    ),
                    max_expanded_operations=max_expanded_operations,
                ),
            )
            prepared_by_digest[digest] = retained
        entries.append(
            PreparedQuantumTargetEntry(
                replace(
                    retained.target_entry,
                    id=TargetCompileEntryId(local_id),
                )
            )
        )
    return tuple(entries)


def _compiled_point_program_digest(
    program: quantum.Program,
    point: _CompiledQuantumPoint,
) -> str:
    return stable_content_hash(
        content_fingerprint(
            {
                "schema": "reference_lab.quantum_point_program.v1",
                "program_id": program.id,
                "verified": point.bound.verified,
                "implementations": point.implementations,
            }
        )
    )


def _retarget_target_entries(
    program: quantum.Program,
    points: tuple[_CompiledQuantumPoint, ...],
    entries: tuple[PreparedQuantumTargetEntry, ...],
) -> tuple[PreparedQuantumTargetEntry, ...]:
    """Normalize request-local entry occurrence ids without re-lowering pulses."""

    occurrences: dict[str, int] = {}
    selected: list[PreparedQuantumTargetEntry] = []
    for point, entry in zip(points, entries, strict=True):
        digest = _compiled_point_program_digest(program, point)
        occurrence = occurrences.get(digest, 0)
        occurrences[digest] = occurrence + 1
        selected.append(
            PreparedQuantumTargetEntry(
                TargetCompileEntry(
                    id=TargetCompileEntryId(
                        f"{program.id}.content-{digest[:16]}.entry-{occurrence}"
                    ),
                    program=entry.program,
                )
            )
        )
    return tuple(selected)


def _result_address(
    entry: PreparedQuantumTargetEntry,
    result: quantum.MeasurementResult,
) -> QuantumTargetResultAddress:
    addresses = tuple(
        address
        for address in entry.acquisition_addresses
        if address.slot_id.local_id == result.id
    )
    expected_count = 1 if result.entity_set is None else None
    if not addresses or (
        expected_count is not None and len(addresses) != expected_count
    ):
        raise ValueError("quantum lab results must cover their acquisitions")
    return QuantumTargetResultAddress(addresses)


def _measurement_results(
    program: quantum.Program,
) -> tuple[quantum.MeasurementResult, ...]:
    return tuple(program.results)


def _realize(
    mapped_target: MappedListModeTarget,
    executed: DomainExecutionResult[ListModeRun],
):
    return realize_executed_measurements(mapped_target, executed)


__all__ = [
    "QuantumJobRuntimeContext",
    "QuantumJobRuntimeSelection",
    "QuantumJobRuntimeSelector",
    "QuantumLabCompiler",
]
