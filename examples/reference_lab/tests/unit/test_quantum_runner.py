from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import assert_type, cast
from unittest.mock import patch

import pytest
import scopecat as sc
from scopecat.compiler.bind import bind_program
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.config.environment import build_config_environment
from scopecat.execution.evidence import (
    domain_execution_evidence_ref,
    instrument_state_evidence_ref,
)
from scopecat.execution.local.program import ApplyStateOperation
from scopecat.execution.program import RunCoverageEffect, RunDomainJob
from scopecat.inspection import CompiledProgramInspectionQuery
from scopecat.kernel.errors import CheckFailed
from scopecat.planning.compilation import compile_run_program
from scopecat.planning.provider_binding import resolve_instrument_contract_catalog
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.execution import InstrumentStateEvidence
from scopecat.sdk.domain import DomainExecutionEvidence
from scopecat_quantum import authoring as quantum
from scopecat_quantum.measurement_computes import (
    BinaryIqProbabilityProducts,
)
from scopecat_testkit.instrument_host import compose_test_instruments
from scopecat_testkit.server.in_process_lab import in_process_lab

import reference_lab.compiler as reference_compiler_module
from reference_lab.bench_interfaces import (
    ANALOG_WAVEFORM_OUTPUT,
    ANALOG_WAVEFORM_OUTPUT_RESET,
)
from reference_lab.compiler import QuantumLabCompiler
from reference_lab.configuration import bootstrap_config
from reference_lab.parameters import QubitParameters
from reference_lab.payloads import reference_lab_payload_codecs
from reference_lab.provider import ReferenceLabProvider
from reference_lab.quantum_runner import (
    prepare_quantum_hardware,
    quantum_capture,
    run_quantum,
)
from reference_lab.targets.list_mode import (
    ConfiguredRoutePlacementProvider,
    ListModeDeviceSnapshot,
    ListModeDomainJobRuntime,
    ListModePlacementDecision,
    MappedListModeTarget,
    configured_list_mode_target,
    point_realization_fingerprint,
)
from reference_lab.virtual_lab.execution import virtual_quantum_job_runtime
from reference_lab.workflows.drag_beta_calibration import (
    drag_beta_program,
    drag_readout_pulse,
)
from reference_lab.workflows.drag_beta_experiment import drag_beta_experiment
from reference_lab.workflows.ramsey_experiments import q0_fixed_if_lo_sweep


@quantum.program(id="reference_lab.test.parallel_set_readout")
def _parallel_set_readout(
    targets: quantum.QubitSet,
) -> quantum.QuantumFragment:
    def readout(qubit: quantum.Qubit) -> quantum.QuantumFragment:
        return quantum.parallel(
            drag_readout_pulse(qubit),
            quantum.acquire(
                qubit,
                duration=sc.Quantity(8, "ns"),
                result="iq_shots",
            ),
        )

    return quantum.parallel_each(
        targets,
        readout,
    )


@sc.experiment(id="reference_lab.test.parallel_set_readout")
def _parallel_set_readout_experiment(
    experiment: sc.ExperimentContext,
) -> None:
    prepare_quantum_hardware(experiment)
    results = experiment.use(
        _parallel_set_readout(("q0", "q1"))
        .with_shots(7)
        .with_compiler_inputs(qubits=sc.parameter_table_ref(QubitParameters))
    )
    experiment.alias(results.iq_shots)


@sc.experiment
def _reset_guard_before_quantum(experiment: sc.ExperimentContext) -> None:
    prepare_quantum_hardware(experiment)
    guard = sc.capability_resource(
        experiment,
        "reset-iq-offset-guard",
        requires=(ANALOG_WAVEFORM_OUTPUT,),
        role="iq-offset-guard",
    )
    guard.invoke(ANALOG_WAVEFORM_OUTPUT_RESET)
    results = experiment.use(
        drag_beta_program(qubit="q0", amplification=2, beta=sc.Quantity(0.5, "ns"))
        .with_shots(7)
        .with_compiler_inputs(qubits=sc.parameter_table_ref(QubitParameters))
    )
    experiment.alias(results.iq_shots)


def _configured_target(
    config: ConfigProfileSnapshot,
    provider: ReferenceLabProvider,
):
    catalog = resolve_instrument_contract_catalog(
        config=config,
        provider_id=provider.provider_id,
        describe=provider.describe,
    )
    return configured_list_mode_target(config, catalog)


class _CompositionPlacementProvider:
    id = "test.composition-placement.v1"
    fingerprint = "sha256:test-composition-placement-v1"

    def __init__(self) -> None:
        self.calls = 0
        self._configured = ConfiguredRoutePlacementProvider()

    def place(
        self,
        selected_signals: tuple[tuple[str, str, str], ...],
        snapshot: ListModeDeviceSnapshot,
    ) -> ListModePlacementDecision:
        self.calls += 1
        return replace(
            self._configured.place(selected_signals, snapshot),
            provider_id=self.id,
            provider_fingerprint=self.fingerprint,
        )


def _with_dsp_policy(
    config: ConfigProfileSnapshot,
    policy: str,
) -> ConfigProfileSnapshot:
    target = config.domain_target
    assert target is not None
    configuration = target.configuration.copy()
    capabilities = configuration["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities = capabilities.copy()
    capabilities["acquisition_dsp_policy"] = policy
    configuration["capabilities"] = capabilities
    return config.model_copy(
        update={
            "system": config.system.model_copy(
                update={
                    "domain_target": target.model_copy(
                        update={"configuration": configuration}
                    )
                }
            )
        }
    )


def _with_max_list_entries(
    config: ConfigProfileSnapshot,
    maximum: int,
) -> ConfigProfileSnapshot:
    target = config.domain_target
    assert target is not None
    configuration = target.configuration.copy()
    capabilities = configuration["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities = capabilities.copy()
    capabilities["max_list_entries"] = maximum
    configuration["capabilities"] = capabilities
    return config.model_copy(
        update={
            "system": config.system.model_copy(
                update={
                    "domain_target": target.model_copy(
                        update={"configuration": configuration}
                    )
                }
            )
        }
    )


def test_quantum_compiler_uses_a_one_point_initial_probe() -> None:
    config = bootstrap_config()
    provider = ReferenceLabProvider(seed=7)
    compiler = QuantumLabCompiler(target=_configured_target(config, provider))

    assert compiler.initial_batch_preparation_limits(1000).max_points == 1


def test_parallel_qubit_set_compiles_to_one_entity_axis_result_group() -> None:
    config = bootstrap_config()
    provider = ReferenceLabProvider(seed=7)
    composition = compose_test_instruments(
        config=config,
        provider=provider,
        domain_compiler=QuantumLabCompiler(
            target=_configured_target(config, provider),
        ),
        payload_codecs=reference_lab_payload_codecs(),
    )
    bound = bind_program(
        compile_invocation(_parallel_set_readout_experiment()).program,
        build_config_environment(config),
    )

    [product] = bound.bindings.product_defs
    assert [axis.kind for axis in product.axes] == ["entity", "shot"]
    assert product.axes[0].entities is not None
    assert [entity.id for entity in product.axes[0].entities] == ["q0", "q1"]

    plan = compile_run_program(composition.system, bound=bound)
    [job] = tuple(
        operation for operation in plan.coverage if isinstance(operation, RunDomainJob)
    )
    mapped = cast("MappedListModeTarget", job.execution.invocation.payload)
    [result] = mapped.mapping.results
    assert len(result.result_address.acquisitions) == 2
    assert [
        address.slot_id.scope for address in result.result_address.acquisitions
    ] == [("targets", "q0"), ("targets", "q1")]
    artifact = mapped.artifact
    assert artifact.placement.logical_qubit_ids == ("q0", "q1")
    assert (
        artifact.placement.device_snapshot_fingerprint
        == artifact.device_snapshot.snapshot_fingerprint
    )
    assert len(artifact.placement.events) == 4
    shared_constraints = tuple(
        constraint
        for constraint in artifact.placement.constraints
        if constraint.kind == "shared_endpoint"
    )
    assert any(
        constraint.entity_ids == ("q0", "q1")
        and any(
            resource.startswith("readout-awg:") for resource in constraint.resource_ids
        )
        for constraint in shared_constraints
    )
    # q0 and q1 are frequency-multiplexed on one I/Q pair and digitizer input.
    assert len(artifact.physical_footprint.waveform_outputs) == 2
    assert len(artifact.physical_footprint.acquisition_inputs) == 1
    assert artifact.instrument_ids == artifact.physical_footprint.instrument_ids
    assert job.execution.next_batch_max_points == (
        artifact.compilation_budget.next_batch_max_points
    )


def _logical_measurement_values(
    tmp_path: Path,
    config: ConfigProfileSnapshot,
) -> tuple[object, ...]:
    provider = ReferenceLabProvider(seed=7)
    composition = compose_test_instruments(
        config=config,
        provider=provider,
        domain_compiler=QuantumLabCompiler(
            target=_configured_target(config, provider),
            job_runtime_selector=virtual_quantum_job_runtime,
        ),
        payload_codecs=reference_lab_payload_codecs(),
    )
    lab = in_process_lab(
        tmp_path,
        config=config,
        system=composition.system,
        instrument_backend=composition.backend,
    )
    run = lab.prepare(drag_beta_experiment()).run()
    return tuple(
        (record.coordinates, record.observables)
        for record in run.measurements().records
    )


def test_lab_runner_places_the_reusable_capture_module() -> None:
    call = drag_beta_program(
        qubit="q0",
        amplification=2,
        beta=sc.Quantity(0.5, "ns"),
    ).with_shots(7)

    capture = assert_type(
        quantum_capture(call),
        sc.ModuleInvocation[BinaryIqProbabilityProducts],
    )
    invocation = run_quantum(call)
    logical = compile_invocation(invocation).program.program

    assert capture.instance_id == "capture"
    assert {
        port.selector.role.role_id
        for port in capture.module.definition.interface.resources
    } == {
        "drive-i",
        "drive-lo",
        "drive-q",
        "iq-offset-guard",
        "readout-i",
        "readout-lo",
        "readout-q",
    }
    [child] = invocation.definition.body.child_instances
    assert child.instance_id == "capture"
    [execution] = logical.domain_executions
    assert [name for name, _value_id in execution.compiler_inputs] == ["qubits"]
    assert [record.record_id for record in logical.product_record_selections] == [
        "probability_0",
        "probability_1",
    ]
    assert [compute.id.qualified_name for compute in logical.measurement_computes] == [
        "capture/binary-iq-probability"
    ]


def test_fixed_experiment_and_structural_runner_share_lab_measurement_policy() -> None:
    direct = compile_invocation(
        run_quantum(
            drag_beta_program(
                qubit="q0",
                amplification=2,
                beta=sc.Quantity(0.5, "ns"),
            ).with_shots(7)
        )
    ).program.program
    fixed = compile_invocation(drag_beta_experiment()).program.program

    assert [record.record_id for record in direct.product_record_selections] == [
        "probability_0",
        "probability_1",
    ]
    assert [record.record_id for record in fixed.product_record_selections] == [
        "probabilities/probability_0",
        "probabilities/probability_1",
    ]
    assert [compute.id.qualified_name for compute in direct.measurement_computes] == [
        compute.id.qualified_name for compute in fixed.measurement_computes
    ]


def test_quantum_target_executes_through_reserved_bare_instruments(
    tmp_path: Path,
) -> None:
    config = bootstrap_config()
    provider = ReferenceLabProvider(seed=7)
    composition = compose_test_instruments(
        config=config,
        provider=provider,
        domain_compiler=QuantumLabCompiler(
            target=_configured_target(config, provider),
            job_runtime_selector=virtual_quantum_job_runtime,
        ),
        payload_codecs=reference_lab_payload_codecs(),
    )
    lab = in_process_lab(
        tmp_path,
        config=config,
        system=composition.system,
        instrument_backend=composition.backend,
    )

    run = lab.prepare(drag_beta_experiment()).run()

    assert run.status == "completed"
    assert len(run.measurements().records) == 15
    state_evidence = lab.services.runs.read_model(
        run.id,
        instrument_state_evidence_ref(),
        InstrumentStateEvidence,
    )
    drive_awg = next(
        state
        for state in state_evidence.final_state
        if state.instrument_id == "drive-awg"
    )
    guard_offset = next(
        observation.value.root
        for observation in drive_awg.observations
        if observation.target.component_path == ("outputs", "ch9")
        and observation.target.property_id == "offset"
    )
    assert guard_offset == sc.Quantity(0.007, "V")
    domain_evidence = lab.services.runs.read_model(
        run.id,
        domain_execution_evidence_ref(),
        DomainExecutionEvidence,
    )
    assert domain_evidence.run_id == run.id
    assert domain_evidence.detail_complete
    assert domain_evidence.attempt_count >= 1
    assert domain_evidence.receipt_count == domain_evidence.attempt_count
    assert domain_evidence.completed_count == domain_evidence.attempt_count
    assert domain_evidence.checkpoint_count == 0


def test_quantum_preview_inspects_only_the_selected_point_without_device_effects(
    tmp_path: Path,
) -> None:
    config = bootstrap_config()
    provider = ReferenceLabProvider(seed=7)
    placement_provider = _CompositionPlacementProvider()
    composition = compose_test_instruments(
        config=config,
        provider=provider,
        domain_compiler=QuantumLabCompiler(
            target=_configured_target(config, provider),
            job_runtime_selector=virtual_quantum_job_runtime,
            placement_provider=placement_provider,
        ),
        payload_codecs=reference_lab_payload_codecs(),
    )
    lab = in_process_lab(
        tmp_path,
        config=config,
        system=composition.system,
        instrument_backend=composition.backend,
    )
    invocation = drag_beta_experiment()
    prepared = lab.prepare(invocation)

    preview = prepared.preview(point="last")

    assert lab.runs().items == ()
    assert preview.selected_point is not None
    assert preview.selected_point.point_index == 14
    [inspection] = preview.domain_inspections
    assert inspection.point_index == 14
    assert inspection.content.schema_id == ("scopecat.compiled_artifact_inspection.v2")
    assert inspection.content.kind == "reference_lab.list_mode.v1"
    assert inspection.content.program is not None
    inspection_snapshot_id = inspection.content.program.snapshot_id
    assert inspection_snapshot_id == inspection.artifact_fingerprint
    inspection_facts = {fact.id: fact.value for fact in inspection.content.facts}
    assert inspection_facts["compile_cache.artifact.outcome"] == "miss"
    assert inspection_facts["compile_cache.semantic.outcome"] == "miss"
    assert inspection_facts["compile_cache.placement.outcome"] == "miss"
    assert inspection_facts["compile_cache.layout.outcome"] == "miss"
    assert cast("float", inspection_facts["compile_seconds.artifact"]) >= 0
    retained_bytes = cast(
        "int", inspection_facts["compile_cache.artifact.retained_bytes"]
    )
    assert retained_bytes > 0
    assert retained_bytes <= cast(
        "int",
        inspection_facts["compile_cache.artifact.max_retained_bytes"],
    )
    assert inspection_facts["placement_provider_id"] == placement_provider.id
    assert inspection_facts["placement_provider_fingerprint"] == (
        placement_provider.fingerprint
    )
    assert placement_provider.calls == 1
    assert tuple(layer.id for layer in inspection.content.program.layers) == (
        "authored",
        "logical",
        "scheduled",
        "physical",
    )
    assert any(
        link.relation == "placed_on" for link in inspection.content.program.links
    )
    physical_layer = inspection.content.program.layers[-1]
    assert physical_layer.kind == "physical"
    assert physical_layer.nodes[0].resource_ids
    assert any(node.kind == "placement_constraint" for node in physical_layer.nodes)
    assert any(
        node.kind == "placement_candidate_selected" for node in physical_layer.nodes
    )
    assert any(
        node.kind == "placement_candidate_rejected" and node.warnings
        for node in physical_layer.nodes
    )
    assert any(
        link.relation == "selected_route" for link in inspection.content.program.links
    )
    [entry] = inspection.content.points
    assert entry.target_entry_id.startswith("drag-beta-rough-calibration.content-")
    assert entry.target_entry_id.endswith(".entry-0")

    selected_again = lab.prepare(invocation).preview(
        coordinates=preview.selected_point.coordinates
    )
    assert selected_again.selected_point == preview.selected_point
    [selected_again_inspection] = selected_again.domain_inspections
    selected_again_facts = {
        fact.id: fact.value for fact in selected_again_inspection.content.facts
    }
    assert selected_again_facts["compile_cache.artifact.outcome"] == "hit"
    assert selected_again_facts["compile_cache.semantic.outcome"] == "not_checked"

    queried = prepared.preview(
        point="last",
        inspection_query=CompiledProgramInspectionQuery(
            layer_id="scheduled",
            snapshot_id=inspection_snapshot_id,
            kind="play",
            limit=1,
        ),
    )
    [queried_inspection] = queried.domain_inspections
    assert queried_inspection.content.program is not None
    queried_layers = {
        layer.id: layer for layer in queried_inspection.content.program.layers
    }
    assert all(
        not layer.nodes
        for layer_id, layer in queried_layers.items()
        if layer_id != "scheduled"
    )
    assert [node.kind for node in queried_layers["scheduled"].nodes] == ["play"]
    assert queried_layers["scheduled"].page.returned_node_count == 1
    assert queried_layers["scheduled"].page.snapshot_id == inspection_snapshot_id

    initial_program = inspection.content.program
    logical_link = next(
        link for link in initial_program.links if link.relation == "lowers_to"
    )
    placed_link = next(
        link for link in initial_program.links if link.relation == "placed_on"
    )
    candidate_link = next(
        link for link in initial_program.links if link.relation == "selected_route"
    )
    for layer_id, node_id, expected_link in (
        (logical_link.source_layer_id, logical_link.source_node_id, logical_link),
        (placed_link.source_layer_id, placed_link.source_node_id, placed_link),
        (candidate_link.target_layer_id, candidate_link.target_node_id, candidate_link),
    ):
        exact = prepared.preview(
            point="last",
            inspection_query=CompiledProgramInspectionQuery(
                layer_id=layer_id,
                snapshot_id=inspection_snapshot_id,
                node_id=node_id,
                limit=1,
            ),
        )
        [exact_inspection] = exact.domain_inspections
        assert exact_inspection.content.program is not None
        exact_program = exact_inspection.content.program
        selected_layer = next(
            layer for layer in exact_program.layers if layer.id == layer_id
        )
        assert [node.id for node in selected_layer.nodes] == [node_id]
        assert expected_link in exact_program.links

    free = lab.prepare(invocation).preview(
        coordinates={
            "beta": sc.Quantity(0.137, "ns"),
            "amplification": 2,
        },
        coordinate_mode="free",
    )
    assert free.selected_point is not None
    assert free.selected_point.point_index is None
    assert free.selected_point.coordinates == {
        "beta": sc.Quantity(0.137, "ns"),
        "amplification": 2,
    }
    [free_inspection] = free.domain_inspections
    assert free_inspection.point_index is None
    [free_entry] = free_inspection.content.points
    assert free_entry.target_entry_id.startswith("drag-beta-rough-calibration.content-")
    assert free_entry.target_entry_id.endswith(".entry-0")

    bound = bind_program(
        compile_invocation(invocation).program,
        build_config_environment(config),
    )
    plan = compile_run_program(composition.system, bound=bound)
    actual_job = next(
        operation
        for operation in plan.coverage
        if isinstance(operation, RunDomainJob) and 14 in operation.point_ordinals
    )
    actual = cast(
        "MappedListModeTarget",
        actual_job.execution.invocation.payload,
    ).artifact
    actual_entry = actual.entries[actual_job.point_ordinals.index(14)]
    preview_hashes = {
        waveform.channel_id: waveform.samples_sha256 for waveform in entry.waveforms
    }
    actual_hashes = {
        waveform.channel_id.value: waveform.samples_sha256
        for waveform in actual_entry.waveforms
    }
    assert preview_hashes == actual_hashes
    assert entry.realization_fingerprint == point_realization_fingerprint(
        actual,
        actual_entry,
    )


def test_target_and_device_dsp_follow_the_same_integrated_iq_semantics(
    tmp_path: Path,
) -> None:
    config = bootstrap_config()

    target_values = _logical_measurement_values(
        tmp_path / "target-dsp",
        _with_dsp_policy(config, "target"),
    )
    device_values = _logical_measurement_values(
        tmp_path / "device-dsp",
        _with_dsp_policy(config, "device"),
    )

    assert device_values == target_values


def test_quantum_scan_results_are_invariant_to_target_batch_boundaries(
    tmp_path: Path,
) -> None:
    config = bootstrap_config()

    one_entry_batches = _logical_measurement_values(
        tmp_path / "one-entry-batches",
        _with_max_list_entries(config, 1),
    )
    complete_batch = _logical_measurement_values(
        tmp_path / "complete-batch",
        config,
    )

    assert one_entry_batches == complete_batch


def test_reviewed_los_prepare_once_without_fragmenting_quantum_batches() -> None:
    config = bootstrap_config()
    provider = ReferenceLabProvider(seed=7)
    target = _configured_target(config, provider)
    composition = compose_test_instruments(
        config=config,
        provider=provider,
        domain_compiler=QuantumLabCompiler(target=target),
        payload_codecs=reference_lab_payload_codecs(),
    )
    bound = bind_program(
        compile_invocation(drag_beta_experiment()).program,
        build_config_environment(config),
    )

    plan = compile_run_program(composition.system, bound=bound)
    coverage = tuple(plan.coverage)

    state_effects = tuple(
        operation
        for covered in coverage
        if isinstance(covered, RunCoverageEffect)
        and isinstance(operation := covered.operation, ApplyStateOperation)
    )
    jobs = tuple(
        operation for operation in coverage if isinstance(operation, RunDomainJob)
    )
    assert all(
        type(job.execution.job_runtime) is ListModeDomainJobRuntime for job in jobs
    )
    assert [effect.instrument_id for effect in state_effects] == [
        "drive-awg",
        "readout-awg",
        "drive-lo-a",
        "drive-lo-b",
        "readout-lo",
    ]
    assert [job.point_ordinals for job in jobs] == [(0,), tuple(range(1, 15))]
    assert plan.domain_target_requirement is not None
    assert "drive-lo-a" not in plan.domain_target_requirement.instrument_ids
    assert "drive-lo-b" not in plan.domain_target_requirement.instrument_ids
    assert "readout-lo" not in plan.domain_target_requirement.instrument_ids
    assert {requirement.id for requirement in plan.resource_requirements} >= {
        "drive-lo-a",
        "drive-lo-b",
        "readout-lo",
    }
    host_addresses = {
        (
            effect.instrument_id,
            target.interface_id,
            target.component_path,
            target.property_id,
        )
        for effect in state_effects
        for target in effect.targets
        if effect.instrument_id in {"drive-awg", "readout-awg"}
    }
    domain_addresses = {
        (
            write.instrument_id,
            write.interface_id,
            write.component_path,
            write.property_id,
        )
        for job in jobs
        for write in job.execution.realtime_write_footprint
    }
    requirement_addresses = {
        (
            requirement.address.instrument_id,
            requirement.address.interface_id,
            requirement.address.component_path,
            requirement.address.property_id,
        )
        for job in jobs
        for requirement in job.execution.state_requirements
    }
    assert {address[0] for address in host_addresses} == {
        "drive-awg",
        "readout-awg",
    }
    assert {address[0] for address in domain_addresses} >= {
        "drive-awg",
        "readout-awg",
    }
    assert host_addresses.isdisjoint(domain_addresses)
    assert requirement_addresses == host_addresses
    assert all(job.execution.setup is not None for job in jobs)
    assert {
        (
            address.instrument_id,
            address.interface_id,
            address.component_path,
            address.property_id,
        )
        for job in jobs
        for address in job.execution.setup_state_invalidations
    } == requirement_addresses


def test_guard_reset_invalidates_state_required_by_quantum_domain() -> None:
    config = bootstrap_config()
    provider = ReferenceLabProvider(seed=7)
    target = _configured_target(config, provider)
    composition = compose_test_instruments(
        config=config,
        provider=provider,
        domain_compiler=QuantumLabCompiler(target=target),
        payload_codecs=reference_lab_payload_codecs(),
    )
    bound = bind_program(
        compile_invocation(_reset_guard_before_quantum()).program,
        build_config_environment(config),
    )

    plan = compile_run_program(composition.system, bound=bound)
    with pytest.raises(CheckFailed) as captured:
        tuple(plan.coverage)

    assert {problem.code for problem in captured.value.problems} == {
        "domain_state_requirement_missing"
    }
    assert captured.value.problems[0].details["state_address"] == (
        "drive-awg:reference_lab.analog_waveform_output/v1/outputs/ch9.offset"
    )
    assert captured.value.problems[0].details["invalidated_by"] == {
        "kind": "host_operation",
        "instrument_id": "drive-awg",
        "interface_id": "reference_lab.analog_waveform_output/v1",
        "component_path": ("outputs", "ch9"),
        "operation_id": "reset",
        "point_index": 0,
    }


def test_fixed_if_lo_sweep_bounds_real_time_batches_with_host_effects() -> None:
    config = bootstrap_config()
    provider = ReferenceLabProvider(seed=7)
    target = _configured_target(config, provider)
    composition = compose_test_instruments(
        config=config,
        provider=provider,
        domain_compiler=QuantumLabCompiler(target=target),
        payload_codecs=reference_lab_payload_codecs(),
    )
    bound = bind_program(
        compile_invocation(q0_fixed_if_lo_sweep()).program,
        build_config_environment(config),
    )

    plan = compile_run_program(composition.system, bound=bound)
    compile_points = reference_compiler_module._compile_points
    with (
        patch.object(
            reference_compiler_module,
            "_compile_points",
            wraps=compile_points,
        ) as compile_points_probe,
        patch.object(
            quantum,
            "bind",
            wraps=quantum.bind,
        ) as bind_probe,
    ):
        coverage = tuple(plan.coverage)

    state_effects = tuple(
        operation
        for covered in coverage
        if isinstance(covered, RunCoverageEffect)
        and isinstance(operation := covered.operation, ApplyStateOperation)
    )
    jobs = tuple(
        operation for operation in coverage if isinstance(operation, RunDomainJob)
    )
    assert {effect.instrument_id for effect in state_effects} == {
        "drive-awg",
        "drive-lo-a",
        "readout-awg",
        "readout-lo",
    }
    assert [job.point_ordinals for job in jobs] == [(0,), (1,), (2,)]
    assert compile_points_probe.call_count == 2
    assert bind_probe.call_count == 2
    assert all(job.execution.setup_residency_requirements for job in jobs)
    assert len({job.execution.setup_residency_requirements for job in jobs}) == 1
    assert all(job.execution.transition_policy == "abnormal_only" for job in jobs)
    assert plan.domain_target_requirement is not None
    assert plan.domain_target_requirement.instrument_ids == (
        "drive-awg",
        "readout-awg",
        "readout-digitizer",
        "timing-controller",
    )
    assert {requirement.id for requirement in plan.resource_requirements} == {
        "drive-awg",
        "drive-lo-a",
        "drive-lo-b",
        "readout-awg",
        "readout-digitizer",
        "readout-lo",
        "timing-controller",
    }
