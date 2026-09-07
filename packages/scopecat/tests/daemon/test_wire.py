# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

import pyarrow as pa
import pytest
from pydantic import ValidationError
from scopecat_testkit.domain import domain_execution_identity
from scopecat_testkit.workflow_fixtures import load_config

from scopecat.analysis.datasets import DerivedDataset
from scopecat.config.changes import parameter_change_proposal_from_updates
from scopecat.config.inventory import (
    InstrumentInventoryRekey,
    InstrumentInventoryRemoval,
    InstrumentInventoryRenameRekey,
)
from scopecat.config.parameters import replace_scalar_parameter
from scopecat.config.registry.records import (
    ConfigActivationOperation,
    ConfigPublishOperation,
    ConfigRegistryActivationRecord,
    ConfigRegistryEntry,
    CrossRunCandidateAcceptance,
    DirectConfigRegistrySource,
)
from scopecat.control.models import (
    PointCoordinateSpec,
    ResourceKey,
    RunDomainTargetRequirement,
    RunPlanSummary,
    RunResourceRequirement,
)
from scopecat.daemon.wire import (
    AnalysisDatasetOutputPayload,
    AnalysisFigureOutputPayload,
    AnalysisParameterProposalOutputPayload,
    AnalysisSaveCommand,
    AnalysisTableOutputPayload,
    AttentionResolutionCommand,
    AttentionResolutionReceipt,
    CandidateConfigRevisionSource,
    ConfigActivationReceipt,
    ConfigEntryActivationCommand,
    ConfigPublishCommand,
    ConfigPublishReceipt,
    DirectConfigRevisionSource,
    ExecutorLease,
    InstrumentConfiguredDefaultsApplyCommand,
    InstrumentInventoryMigrationCommand,
    InstrumentInventoryMigrationReceipt,
    InstrumentSessionLeaseReceipt,
    InstrumentSessionOpenReceipt,
    RunCoverageAdvanceCommand,
    RunCoverageState,
    RunDomainJobStatePage,
    RunDomainJobStateView,
    RunDomainJobTransitionBatchCommand,
    RunDomainJobTransitionBatchReceipt,
    RunDomainJobTransitionItem,
    RunDomainJobTransitionPage,
    RunDomainJobTransitionView,
    RunHardwareBatchCommand,
    RunHardwareFinishCommand,
    RunInstrumentProvisionCommand,
    RunInstrumentProvisionReceipt,
    RunRecoveryGroupCommitCommand,
    RunRecoveryGroupCommitReceipt,
    RunRecoveryGroupView,
    RunSubmission,
)
from scopecat.kernel.content_identity import sha256_content_hash
from scopecat.kernel.problems import Problem, ProblemPhase
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.state import StateValue
from scopecat.records.analysis import (
    MAX_ANALYSIS_OUTPUTS,
    AnalysisDatasetViewSource,
    AnalysisExecution,
    AnalysisExecutionInput,
    AnalysisExecutionOutput,
    AnalysisExecutionOutputReference,
    AnalysisField,
    AnalysisFigureLayerSpec,
    AnalysisFigureProjection,
    AnalysisFigureViewSpec,
    AnalysisTableViewSpec,
    ProjectAnalysisDecisionReference,
)
from scopecat.records.config import config_content_hash
from scopecat.records.execution import (
    DomainExecutionReceipt,
    DomainJobCheckpoint,
    DomainJobCheckpointTransition,
    DomainJobInvocationTransition,
    DomainJobTerminalTransition,
    RecoveryGroupCompletion,
)
from scopecat.records.instrument import InstrumentStateSnapshot, state_member_target
from scopecat.records.run import ConfigRegistryRunConfigSource
from scopecat.records.run_request import RunRequest
from scopecat.sdk.instruments import (
    InstrumentConfiguredDefaultsApplyReceipt,
    InstrumentDescription,
)
from scopecat.sdk.instruments.commands import InstrumentStateAssignment
from scopecat.sdk.instruments.execution import RunHardwareApply, RunHardwareBatch
from scopecat.sdk.instruments.members import PropertyRef


def _request() -> RunRequest:
    return RunRequest(
        experiment_id="scratch",
        inputs={"bias": 0.25},
    )


def test_config_registry_commands_are_closed_typed_json() -> None:
    config = load_config()
    entry = ConfigRegistryEntry(
        id="baseline",
        config_ref="config-registry/entries/baseline/config.json",
        content_hash=config_content_hash(config),
        source=DirectConfigRegistrySource(),
        actor="notebook",
    )
    activation = ConfigRegistryActivationRecord(
        generation=1,
        action="activation",
        entry_id=entry.id,
        entry_content_hash=entry.content_hash,
        actor="operator",
    )
    operation = ConfigActivationOperation(
        operation_id="activate-baseline",
        intent_hash=ConfigEntryActivationCommand(
            operation_id="activate-baseline",
            entry_id=entry.id,
            actor="operator",
            expected_generation=0,
        ).intent_hash,
        entry_id=entry.id,
        expected_generation=0,
        actor="operator",
        activation_generation=activation.generation,
    )
    activated = ConfigActivationReceipt(
        operation=operation,
        activation=activation,
    )
    publish_command = ConfigPublishCommand(
        operation_id="publish-baseline",
        source=DirectConfigRevisionSource(config=config),
        entry_id=entry.id,
        actor="notebook",
        expected_generation=0,
    )
    published = ConfigPublishReceipt(
        operation=ConfigPublishOperation(
            operation_id=publish_command.operation_id,
            intent_hash=publish_command.intent_hash,
            source_intent_hash=publish_command.source_intent_hash,
            entry_id=publish_command.entry_id,
            expected_generation=publish_command.expected_generation,
            actor=publish_command.actor,
            note=publish_command.note,
            activation_generation=activation.generation,
        ),
        entry=entry,
        activation=activation,
    )
    activation_command = ConfigEntryActivationCommand(
        operation_id="activate-baseline",
        entry_id=entry.id,
        actor="operator",
        expected_generation=0,
    )

    assert (
        ConfigActivationReceipt.model_validate_json(activated.model_dump_json())
        == activated
    )
    assert (
        ConfigPublishReceipt.model_validate_json(published.model_dump_json())
        == published
    )
    assert (
        ConfigEntryActivationCommand.model_validate_json(
            activation_command.model_dump_json()
        )
        == activation_command
    )
    assert (
        ConfigPublishCommand.model_validate_json(publish_command.model_dump_json())
        == publish_command
    )


def test_config_activation_operation_binds_intent_and_result_generation() -> None:
    first = ConfigEntryActivationCommand(
        operation_id="activation-1",
        entry_id="baseline",
        actor="operator",
        expected_generation=3,
        note="select baseline",
    )
    replay_key = first.model_copy(update={"operation_id": "activation-2"})

    assert first.intent_hash == replay_key.intent_hash
    with pytest.raises(ValidationError, match="intent hash is inconsistent"):
        ConfigActivationOperation(
            operation_id=first.operation_id,
            intent_hash=f"sha256:{'0' * 64}",
            entry_id=first.entry_id,
            expected_generation=first.expected_generation,
            actor=first.actor,
            note=first.note,
            activation_generation=4,
        )
    with pytest.raises(ValidationError, match="observed or next generation"):
        ConfigActivationOperation(
            operation_id=first.operation_id,
            intent_hash=first.intent_hash,
            entry_id=first.entry_id,
            expected_generation=first.expected_generation,
            actor=first.actor,
            note=first.note,
            activation_generation=5,
        )


def test_config_publish_operation_binds_canonical_intent_and_result() -> None:
    config = load_config()
    first = ConfigPublishCommand(
        operation_id="publish-1",
        source=DirectConfigRevisionSource(config=config),
        entry_id="baseline",
        actor="operator",
        expected_generation=3,
        note="publish baseline",
    )
    replay_key = first.model_copy(update={"operation_id": "publish-2"})

    assert first.intent_hash == replay_key.intent_hash
    assert first.source_intent_hash == replay_key.source_intent_hash
    assert first.intent_hash != first.model_copy(update={"note": "changed"}).intent_hash
    with pytest.raises(ValidationError, match="intent hash is inconsistent"):
        ConfigPublishOperation(
            operation_id=first.operation_id,
            intent_hash=f"sha256:{'0' * 64}",
            source_intent_hash=first.source_intent_hash,
            entry_id=first.entry_id,
            expected_generation=first.expected_generation,
            actor=first.actor,
            note=first.note,
            activation_generation=4,
        )
    with pytest.raises(ValidationError, match="observed or next generation"):
        ConfigPublishOperation(
            operation_id=first.operation_id,
            intent_hash=first.intent_hash,
            source_intent_hash=first.source_intent_hash,
            entry_id=first.entry_id,
            expected_generation=first.expected_generation,
            actor=first.actor,
            note=first.note,
            activation_generation=5,
        )


def test_config_publish_receipt_binds_operation_entry_and_activation() -> None:
    config = load_config()
    command = ConfigPublishCommand(
        operation_id="publish-baseline",
        source=DirectConfigRevisionSource(config=config),
        entry_id="baseline",
        actor="operator",
        expected_generation=0,
    )
    entry = ConfigRegistryEntry(
        id=command.entry_id,
        config_ref="config-registry/entries/baseline/config.json",
        content_hash=config_content_hash(config),
        source=DirectConfigRegistrySource(),
        actor=command.actor,
    )
    activation = ConfigRegistryActivationRecord(
        generation=1,
        action="activation",
        entry_id=entry.id,
        entry_content_hash=entry.content_hash,
        actor=command.actor,
    )
    operation = ConfigPublishOperation(
        operation_id=command.operation_id,
        intent_hash=command.intent_hash,
        source_intent_hash=command.source_intent_hash,
        entry_id=command.entry_id,
        expected_generation=command.expected_generation,
        actor=command.actor,
        activation_generation=activation.generation,
    )

    with pytest.raises(ValidationError, match="do not match"):
        ConfigPublishReceipt(
            operation=operation,
            entry=entry,
            activation=activation.model_copy(update={"entry_id": "other"}),
        )


def test_instrument_inventory_migration_is_discriminated_closed_json() -> None:
    config = load_config()
    changes = (
        InstrumentInventoryRemoval(
            instrument_id="retired-source",
            exclusivity_key="retired-source",
        ),
        InstrumentInventoryRekey(
            instrument_id="source-0",
            from_exclusivity_key="source-0",
            to_exclusivity_key="rack-a/source",
        ),
        InstrumentInventoryRenameRekey(
            from_instrument_id="old-meter",
            to_instrument_id="meter-0",
            from_exclusivity_key="old-meter",
            to_exclusivity_key="rack-a/meter",
        ),
    )
    command = InstrumentInventoryMigrationCommand(
        config=config,
        entry_id="inventory-v2",
        changes=changes,
        actor="operator",
        expected_generation=1,
    )
    entry = ConfigRegistryEntry(
        id=command.entry_id,
        config_ref="config-registry/entries/inventory-v2/config.json",
        content_hash=config_content_hash(config),
        source=DirectConfigRegistrySource(),
        actor=command.actor,
    )
    receipt = InstrumentInventoryMigrationReceipt(
        entry=entry,
        activation=ConfigRegistryActivationRecord(
            generation=2,
            action="inventory_migration",
            entry_id=entry.id,
            entry_content_hash=entry.content_hash,
            actor=command.actor,
        ),
        changes=changes,
    )

    restored = InstrumentInventoryMigrationCommand.model_validate_json(
        command.model_dump_json()
    )

    assert restored == command
    assert isinstance(restored.changes[0], InstrumentInventoryRemoval)
    assert isinstance(restored.changes[1], InstrumentInventoryRekey)
    assert isinstance(restored.changes[2], InstrumentInventoryRenameRekey)
    assert (
        InstrumentInventoryMigrationReceipt.model_validate_json(
            receipt.model_dump_json()
        )
        == receipt
    )


def test_instrument_inventory_rekey_rejects_a_noop() -> None:
    with pytest.raises(ValidationError, match="must change"):
        InstrumentInventoryRekey(
            instrument_id="source-0",
            from_exclusivity_key="source-0",
            to_exclusivity_key="source-0",
        )


def test_post_run_commands_are_closed_json_and_bind_proposals_to_runs() -> None:
    proposal = parameter_change_proposal_from_updates(
        source_run_id="run-1",
        source_config=load_config(),
        analysis_title="fit",
        analysis_record_id="analysis-fit-r1",
        proposal_id="drive-frequency",
        updates=(
            replace_scalar_parameter(
                "drive_frequency",
                Quantity(value=5.1, unit="GHz"),
            ),
        ),
        reason="fit converged",
        confidence=0.9,
    )
    dataset = DerivedDataset.from_arrow(
        pa.table({"bias": [1.0, 2.0], "signal": [3.0, 4.0]}),
        fields={"bias": AnalysisField(role="coordinate")},
    )
    command = AnalysisSaveCommand(
        title="fit",
        analysis_key="fit",
        executions=(
            AnalysisExecution(
                id="fit",
                implementation="python:lab.fit",
                deterministic=True,
                inputs=("dataset",),
                input_bindings=(
                    AnalysisExecutionInput(
                        name="dataset",
                        kind="measurement_dataset",
                        target="measurement-dataset",
                        content_hash="sha256:measurements",
                        codec="scopecat.measurement-dataset.v12",
                    ),
                ),
                outputs=(
                    AnalysisExecutionOutput(
                        name="fit",
                        kind="derived_dataset",
                        content_hash=sha256_content_hash(dataset.to_arrow_ipc()),
                        codec="scopecat.derived-dataset.arrow-ipc.v2",
                    ),
                ),
            ),
        ),
        outputs=(
            AnalysisDatasetOutputPayload(
                kind="dataset",
                id="fits",
                title="fit data",
                produced_by=AnalysisExecutionOutputReference(
                    execution_id="fit",
                    output_name="fit",
                ),
                content=dataset.to_payload(),
            ),
            AnalysisFigureOutputPayload(
                kind="figure",
                id="fit-curve",
                title="fit curve",
                content=AnalysisFigureViewSpec(
                    layers=(
                        AnalysisFigureLayerSpec(
                            id="data",
                            source=AnalysisDatasetViewSource(output_id="fits"),
                            projection=AnalysisFigureProjection(
                                kind="line", x="bias", y="signal"
                            ),
                        ),
                    )
                ),
            ),
            AnalysisParameterProposalOutputPayload(
                kind="parameter_change_proposal",
                id=proposal.id,
                title=proposal.id,
                content=proposal,
            ),
        ),
    )
    publish = ConfigPublishCommand(
        operation_id="publish-candidate-fit",
        source=CandidateConfigRevisionSource(
            run_id="run-1",
            proposal_id=proposal.id,
            acceptance=CrossRunCandidateAcceptance(
                decision=ProjectAnalysisDecisionReference(
                    analysis_record_id="analysis-candidate-verification-r1",
                    output_id="decision",
                    schema_id="candidate-verification.v1",
                    schema_hash=f"sha256:{'a' * 64}",
                )
            ),
        ),
        entry_id="candidate-fit",
        actor="operator",
        expected_generation=1,
        note="fit reviewed",
    )

    assert AnalysisSaveCommand.model_validate_json(command.model_dump_json()) == command
    assert "preview" not in command.model_dump_json()
    assert (
        ConfigPublishCommand.model_validate_json(publish.model_dump_json()) == publish
    )
    with pytest.raises(ValidationError, match="identify the command analysis"):
        AnalysisSaveCommand(
            **command.model_dump(exclude={"outputs"}),
            outputs=(
                AnalysisParameterProposalOutputPayload(
                    kind="parameter_change_proposal",
                    id=proposal.id,
                    title=proposal.id,
                    content=proposal.model_copy(
                        update={"analysis_record_id": "analysis-other"}
                    ),
                ),
            ),
        )
    with pytest.raises(
        ValidationError,
        match="producer must identify an execution output",
    ):
        AnalysisSaveCommand(
            **command.model_dump(exclude={"outputs"}),
            outputs=(
                command.outputs[0].model_copy(
                    update={
                        "produced_by": AnalysisExecutionOutputReference(
                            execution_id="fit",
                            output_name="missing",
                        )
                    }
                ),
            ),
        )


def test_analysis_save_command_bounds_output_count() -> None:
    output = AnalysisTableOutputPayload(
        kind="table",
        id="large-table",
        title="large table",
        content=AnalysisTableViewSpec(
            source=AnalysisDatasetViewSource(output_id="fits"),
            columns=("value",),
        ),
    )

    with pytest.raises(ValidationError, match=f"at most {MAX_ANALYSIS_OUTPUTS} items"):
        AnalysisSaveCommand(
            title="too many outputs",
            analysis_key="too-many-outputs",
            outputs=tuple(
                output.model_copy(update={"id": f"output-{index}"})
                for index in range(MAX_ANALYSIS_OUTPUTS + 1)
            ),
        )


def test_run_submission_is_closed_typed_json_without_executable_state() -> None:
    config = load_config()
    source = ConfigRegistryRunConfigSource(
        selector="active",
        entry_id="baseline",
        config_ref="config-registry/configs/baseline.json",
        content_hash=config_content_hash(config),
        registry_generation=2,
    )
    submission = RunSubmission(
        submission_id="submit-1",
        config=config,
        config_source=source,
        request=_request(),
        plan=RunPlanSummary(
            experiment_id="scratch",
            experiment_kind="scratch",
            point_plan_fingerprint="a" * 64,
            measurement_contract_fingerprint="b" * 64,
            point_count=2,
            initial_point_count=2,
            point_limit=2,
            coordinates=(
                PointCoordinateSpec(
                    id="bias",
                    kind="float",
                    sampled_values=(0.0, 1.0),
                ),
            ),
            sampled_points=({"bias": 0.0}, {"bias": 1.0}),
            record_ids=("signal",),
            host_instrument_order=("scope-1",),
            host_provider_id="tests.provider",
            host_contract_fingerprint="0" * 64,
            domain_target_requirement=RunDomainTargetRequirement(
                id="controller-1",
                kind="tests.controller",
                instrument_ids=(),
            ),
            run_resource_requirements=(RunResourceRequirement(id="scope-1"),),
        ),
    )
    restored = RunSubmission.model_validate_json(submission.model_dump_json())
    assert restored == submission
    assert restored.config_source == source
    with pytest.raises(ValidationError):
        ResourceKey.model_validate({"id": "drive-1", "kind": "channel"})
    with pytest.raises(ValidationError, match="unique"):
        RunPlanSummary(
            experiment_id="scratch",
            experiment_kind="scratch",
            point_plan_fingerprint="a" * 64,
            measurement_contract_fingerprint="b" * 64,
            point_count=1,
            initial_point_count=1,
            point_limit=1,
            run_resource_requirements=(
                RunResourceRequirement(id="scope-1"),
                RunResourceRequirement(id="scope-1"),
            ),
        )
    RunPlanSummary(
        experiment_id="scratch",
        experiment_kind="scratch",
        point_plan_fingerprint="a" * 64,
        measurement_contract_fingerprint="b" * 64,
        point_count=1,
        initial_point_count=1,
        point_limit=1,
        run_resource_requirements=(RunResourceRequirement(id="scope-1"),),
    )
    with pytest.raises(ValidationError, match="host instrument order"):
        RunPlanSummary(
            experiment_id="scratch",
            experiment_kind="scratch",
            point_plan_fingerprint="a" * 64,
            measurement_contract_fingerprint="b" * 64,
            point_count=1,
            initial_point_count=1,
            point_limit=1,
            host_instrument_order=("scope-2",),
            run_resource_requirements=(RunResourceRequirement(id="scope-1"),),
        )


def test_domain_target_summary_uses_only_its_instrument_footprint() -> None:
    summary = RunPlanSummary(
        experiment_id="domain",
        experiment_kind="domain",
        point_plan_fingerprint="a" * 64,
        measurement_contract_fingerprint="b" * 64,
        point_count=1,
        initial_point_count=1,
        point_limit=1,
        domain_target_requirement=RunDomainTargetRequirement(
            id="tests.domain.target",
            kind="tests.domain",
            instrument_ids=("source-0",),
        ),
        run_resource_requirements=(RunResourceRequirement(id="source-0"),),
    )

    assert summary.run_resource_requirements == (RunResourceRequirement(id="source-0"),)


def test_domain_target_summary_requires_its_complete_instrument_footprint() -> None:
    with pytest.raises(ValidationError, match="omits domain target instruments"):
        RunPlanSummary(
            experiment_id="domain",
            experiment_kind="domain",
            point_plan_fingerprint="a" * 64,
            measurement_contract_fingerprint="b" * 64,
            point_count=1,
            initial_point_count=1,
            point_limit=1,
            domain_target_requirement=RunDomainTargetRequirement(
                id="tests.domain.target",
                kind="tests.domain",
                instrument_ids=("source-0",),
            ),
            run_resource_requirements=(),
        )


def test_executor_lease_is_expiring_and_fenced() -> None:
    now = datetime.now(UTC)
    lease = ExecutorLease(
        lease_id="lease-1",
        segment_id="segment-1",
        run_id="run-1",
        executor_id="notebook-kernel-1",
        issued_at=now,
        expires_at=now + timedelta(seconds=30),
        heartbeat_interval_seconds=10,
    )

    assert ExecutorLease.model_validate_json(lease.model_dump_json()) == lease
    with pytest.raises(ValidationError, match="expire after"):
        ExecutorLease(
            **lease.model_dump(exclude={"expires_at"}),
            expires_at=now,
        )
    with pytest.raises(ValidationError, match="UTC offset"):
        ExecutorLease(
            **lease.model_dump(exclude={"issued_at"}),
            issued_at=now.replace(tzinfo=None),
        )


def test_run_hardware_commands_bind_fence_and_batch_identity() -> None:
    provision = RunInstrumentProvisionCommand(
        lease_id="lease-1",
        operation_id="lifecycle.provide-instruments",
    )
    receipt = RunInstrumentProvisionReceipt(
        run_id="run-1",
        operation_id=provision.operation_id,
        status="ready",
    )
    batch = RunHardwareBatch(
        operation_id="hardware.batch-1",
        actions=(
            RunHardwareApply(
                effect_id="point-0.apply.source-0",
                point_index=0,
                instrument_id="source-0",
                assignments=(
                    InstrumentStateAssignment(
                        resource_id="source-0",
                        target=state_member_target(
                            PropertyRef(
                                "test.set_frequency/v1",
                                (),
                                "frequency",
                            )
                        ),
                        value=StateValue(Quantity(5.0, "GHz")),
                    ),
                ),
            ),
        ),
    )
    execute = RunHardwareBatchCommand(
        lease_id="lease-1",
        sequence=0,
        batch=batch,
    )
    finish = RunHardwareFinishCommand(
        lease_id="lease-1",
        operation_id="hardware.finish",
        failed=False,
    )

    assert (
        RunInstrumentProvisionCommand.model_validate_json(provision.model_dump_json())
        == provision
    )
    assert (
        RunInstrumentProvisionReceipt.model_validate_json(receipt.model_dump_json())
        == receipt
    )
    assert (
        RunHardwareBatchCommand.model_validate_json(execute.model_dump_json())
        == execute
    )
    assert (
        RunHardwareFinishCommand.model_validate_json(finish.model_dump_json()) == finish
    )
    with pytest.raises(ValidationError, match="effect ids must be unique"):
        RunHardwareBatch(
            operation_id="hardware.duplicate",
            actions=(batch.actions[0], batch.actions[0]),
        )


def test_run_coverage_wire_models_require_a_nonempty_prefix() -> None:
    command = RunCoverageAdvanceCommand(
        lease_id="lease-1",
        start_index=2,
        point_count=3,
    )
    state = RunCoverageState(run_id="run-1", completed_point_count=5)

    assert (
        RunCoverageAdvanceCommand.model_validate_json(command.model_dump_json())
        == command
    )
    assert RunCoverageState.model_validate_json(state.model_dump_json()) == state
    with pytest.raises(ValidationError):
        RunCoverageAdvanceCommand(
            lease_id="lease-1",
            start_index=0,
            point_count=0,
        )


def test_recovery_group_wire_models_preserve_output_proof() -> None:
    completion = RecoveryGroupCompletion(
        schedule_fingerprint="schedule-v1",
        group_id="comparison:0",
        point_indices=(2, 0),
        output_kind="measurement",
        record_content_hashes=("record-2", "record-0"),
    )
    command = RunRecoveryGroupCommitCommand(
        lease_id="lease-1",
        groups=(completion,),
    )
    receipt = RunRecoveryGroupCommitReceipt(
        run_id="run-1",
        items=(
            RunRecoveryGroupView(
                sequence=1,
                run_id="run-1",
                segment_id="segment-1",
                completion=completion,
            ),
        ),
    )

    assert (
        RunRecoveryGroupCommitCommand.model_validate_json(command.model_dump_json())
        == command
    )
    assert (
        RunRecoveryGroupCommitReceipt.model_validate_json(receipt.model_dump_json())
        == receipt
    )
    with pytest.raises(ValidationError, match="record hashes"):
        RecoveryGroupCompletion(
            schedule_fingerprint="schedule-v1",
            group_id="comparison:0",
            point_indices=(2, 0),
            output_kind="measurement",
            record_content_hashes=("record-2",),
        )


def test_attention_resolution_separates_close_from_continuation() -> None:
    close = AttentionResolutionCommand.close_run()
    continuation = AttentionResolutionCommand.continue_run(
        run_contract_fingerprint="a" * 64,
    )

    assert (
        AttentionResolutionCommand.model_validate_json(close.model_dump_json()) == close
    )
    assert (
        AttentionResolutionCommand.model_validate_json(continuation.model_dump_json())
        == continuation
    )
    assert (
        AttentionResolutionReceipt(
            run_id="run-1",
            disposition="close",
            state="closed",
            released_resource_count=1,
        ).state
        == "closed"
    )
    assert (
        AttentionResolutionReceipt(
            run_id="run-1",
            disposition="continue",
            state="queued",
            released_resource_count=1,
        ).state
        == "queued"
    )
    with pytest.raises(ValidationError, match="run contract"):
        AttentionResolutionCommand(disposition="continue")
    with pytest.raises(ValidationError, match="scheduler state"):
        AttentionResolutionReceipt(
            run_id="run-1",
            disposition="continue",
            state="closed",
            released_resource_count=1,
        )


def test_domain_job_transition_wire_models_retain_provider_state() -> None:
    intent, execution_id = domain_execution_identity(
        run_id="run-1",
        logical_compute_node_id="domain.batch-0",
        invocation_id="invocation-1",
        target_intent={"lo_frequency_hz": 5_000_000_000},
    )
    checkpoint = DomainJobCheckpoint(
        execution_key=execution_id.execution_key,
        job_id="provider-job",
        revision=2,
        resume_token={"cursor": "result-page-2"},
        progress={"completed_shots": 128},
    )
    checkpoint_transition = DomainJobCheckpointTransition(checkpoint=checkpoint)
    item = RunDomainJobTransitionItem(
        logical_compute_node_id="domain.batch-0",
        point_ordinals=(2, 3),
        transition=checkpoint_transition,
    )
    command = RunDomainJobTransitionBatchCommand(
        lease_id="lease-1",
        items=(item,),
    )
    invocation_transition = DomainJobInvocationTransition(
        execution_id=execution_id,
        intent=intent,
    )
    invocation_view = RunDomainJobTransitionView(
        sequence=1,
        run_id="run-1",
        logical_compute_node_id=item.logical_compute_node_id,
        point_ordinals=item.point_ordinals,
        transition=invocation_transition,
    )
    checkpoint_view = RunDomainJobTransitionView(
        sequence=2,
        run_id="run-1",
        logical_compute_node_id=item.logical_compute_node_id,
        point_ordinals=item.point_ordinals,
        transition=checkpoint_transition,
    )
    terminal_view = RunDomainJobTransitionView(
        sequence=3,
        run_id="run-1",
        logical_compute_node_id=item.logical_compute_node_id,
        point_ordinals=item.point_ordinals,
        transition=DomainJobTerminalTransition(
            receipt=DomainExecutionReceipt(
                execution_key=checkpoint.execution_key,
                status="completed",
                result_fingerprint="results-v1",
                result_count=2,
            )
        ),
    )
    page = RunDomainJobTransitionPage(
        run_id="run-1",
        items=(invocation_view, checkpoint_view, terminal_view),
    )
    state_view = RunDomainJobStateView(
        run_id="run-1",
        invocation=invocation_transition,
        point_ordinals=item.point_ordinals,
        state="terminal",
        invocation_sequence=invocation_view.sequence,
        latest_sequence=terminal_view.sequence,
        transition_count=3,
        latest_transition=terminal_view.transition,
    )
    state_page = RunDomainJobStatePage(run_id="run-1", items=(state_view,))

    assert (
        RunDomainJobTransitionBatchCommand.model_validate_json(
            command.model_dump_json()
        )
        == command
    )
    batch_receipt = RunDomainJobTransitionBatchReceipt(
        run_id="run-1",
        items=(checkpoint_view,),
    )
    assert (
        RunDomainJobTransitionBatchReceipt.model_validate_json(
            batch_receipt.model_dump_json()
        )
        == batch_receipt
    )
    assert (
        RunDomainJobTransitionPage.model_validate_json(page.model_dump_json()) == page
    )
    assert invocation_transition.intent.target_intent == {
        "lo_frequency_hz": 5_000_000_000
    }
    assert (
        RunDomainJobStatePage.model_validate_json(state_page.model_dump_json())
        == state_page
    )
    with pytest.raises(ValidationError, match="latest transition"):
        RunDomainJobStateView.model_validate(
            {**state_view.model_dump(), "state": "pending"}
        )
    reordered = RunDomainJobTransitionItem(
        logical_compute_node_id="domain.batch-0",
        point_ordinals=(3, 2),
        transition=checkpoint_transition,
    )
    assert reordered.point_ordinals == (3, 2)
    with pytest.raises(ValidationError, match="unique"):
        RunDomainJobTransitionItem(
            logical_compute_node_id="domain.batch-0",
            point_ordinals=(3, 2, 3),
            transition=checkpoint_transition,
        )


def test_run_hardware_apply_rejects_duplicate_physical_targets() -> None:
    assignment = InstrumentStateAssignment(
        resource_id="source-0",
        target=state_member_target(
            PropertyRef("test.set_frequency/v1", (), "frequency")
        ),
        value=StateValue(Quantity(5.0, "GHz")),
        entity_ids=["q0"],
    )

    with pytest.raises(ValidationError, match="property targets must be unique"):
        RunHardwareApply(
            effect_id="point-0.apply.source-0",
            point_index=0,
            instrument_id="source-0",
            assignments=(
                assignment,
                assignment.model_copy(update={"entity_ids": ["q1"]}),
            ),
        )


def test_run_instrument_provision_state_evidence_matches_instrument_order() -> None:
    source_a = InstrumentStateSnapshot(instrument_id="source-a")
    source_b = InstrumentStateSnapshot(instrument_id="source-b")

    receipt = RunInstrumentProvisionReceipt(
        run_id="run-1",
        operation_id="lifecycle.provide-instruments",
        status="ready",
        instrument_ids=("source-a", "source-b"),
        observed_state=(source_a, source_b),
        baseline_state=(source_a, source_b),
    )

    assert (
        RunInstrumentProvisionReceipt.model_validate_json(receipt.model_dump_json())
        == receipt
    )
    with pytest.raises(ValidationError, match="observed state must match"):
        RunInstrumentProvisionReceipt(
            run_id="run-1",
            operation_id="lifecycle.provide-instruments",
            status="ready",
            instrument_ids=("source-a", "source-b"),
            observed_state=(source_b, source_a),
            baseline_state=(source_a, source_b),
        )
    with pytest.raises(ValidationError, match="baseline state must match"):
        RunInstrumentProvisionReceipt(
            run_id="run-1",
            operation_id="lifecycle.provide-instruments",
            status="ready",
            instrument_ids=("source-a", "source-b"),
            observed_state=(source_a, source_b),
            baseline_state=(source_b, source_a),
        )


def test_rejected_run_instrument_provision_has_no_state_evidence() -> None:
    source = InstrumentStateSnapshot(instrument_id="source-a")
    problem = Problem(
        code="instrument_unavailable",
        phase=ProblemPhase.EXECUTION,
        message="instrument unavailable",
    )

    receipt = RunInstrumentProvisionReceipt(
        run_id="run-1",
        operation_id="lifecycle.provide-instruments",
        status="rejected",
        instrument_ids=("source-a",),
        problems=(problem,),
    )

    assert receipt.observed_state == ()
    assert receipt.baseline_state == ()
    with pytest.raises(ValidationError, match="cannot expose state evidence"):
        RunInstrumentProvisionReceipt(
            run_id="run-1",
            operation_id="lifecycle.provide-instruments",
            status="rejected",
            instrument_ids=("source-a",),
            problems=(problem,),
            observed_state=(source,),
        )


def test_instrument_session_open_requires_ordered_observed_state() -> None:
    instrument_ids = ("source-a", "source-b")
    descriptions = tuple(
        InstrumentDescription(
            instrument_id=instrument_id,
            implementation_id="tests.source",
            implementation_version="1",
        )
        for instrument_id in instrument_ids
    )
    observed_state = tuple(
        InstrumentStateSnapshot(instrument_id=instrument_id)
        for instrument_id in instrument_ids
    )
    receipt = InstrumentSessionOpenReceipt(
        session_id="session-1",
        actor="alice",
        config_entry_id="baseline",
        config_content_hash=f"sha256:{'0' * 64}",
        instrument_ids=instrument_ids,
        configured_default_instrument_ids=("source-b",),
        descriptions=descriptions,
        observed_state=observed_state,
        opened_at=datetime(2026, 7, 29, tzinfo=UTC),
        renewed_at=datetime(2026, 7, 29, tzinfo=UTC),
        expires_at=datetime(2026, 7, 29, 0, 1, tzinfo=UTC),
    )

    assert (
        InstrumentSessionOpenReceipt.model_validate_json(receipt.model_dump_json())
        == receipt
    )
    for invalid_state in (
        observed_state[:1],
        tuple(reversed(observed_state)),
    ):
        with pytest.raises(
            ValidationError,
            match="observed state must match instrument_ids in order",
        ):
            InstrumentSessionOpenReceipt(
                **receipt.model_dump(exclude={"observed_state"}),
                observed_state=invalid_state,
            )
    with pytest.raises(ValidationError, match="must follow renewed_at"):
        InstrumentSessionOpenReceipt(
            **receipt.model_dump(exclude={"expires_at"}),
            expires_at=receipt.renewed_at,
        )


def test_instrument_session_lease_requires_an_aware_ordered_window() -> None:
    renewed_at = datetime(2026, 7, 29, tzinfo=UTC)
    receipt = InstrumentSessionLeaseReceipt(
        session_id="session-1",
        renewed_at=renewed_at,
        expires_at=renewed_at + timedelta(minutes=1),
    )

    assert (
        InstrumentSessionLeaseReceipt.model_validate_json(receipt.model_dump_json())
        == receipt
    )
    with pytest.raises(ValidationError, match="renewed_at must include a UTC offset"):
        InstrumentSessionLeaseReceipt(
            session_id="session-1",
            renewed_at=renewed_at.replace(tzinfo=None),
            expires_at=receipt.expires_at,
        )
    with pytest.raises(ValidationError, match="expires_at must include a UTC offset"):
        InstrumentSessionLeaseReceipt(
            session_id="session-1",
            renewed_at=renewed_at,
            expires_at=receipt.expires_at.replace(tzinfo=None),
        )
    with pytest.raises(ValidationError, match="must follow renewed_at"):
        InstrumentSessionLeaseReceipt(
            session_id="session-1",
            renewed_at=renewed_at,
            expires_at=renewed_at,
        )


def test_configured_defaults_apply_command_requires_operation_identity() -> None:
    command = InstrumentConfiguredDefaultsApplyCommand(operation_id="defaults.apply-1")

    assert (
        InstrumentConfiguredDefaultsApplyCommand.model_validate_json(
            command.model_dump_json()
        )
        == command
    )
    with pytest.raises(ValidationError):
        InstrumentConfiguredDefaultsApplyCommand(operation_id="")


@pytest.mark.parametrize("status", ["applied", "unchanged"])
def test_successful_configured_defaults_apply_requires_synchronized_state(
    status: Literal["applied", "unchanged"],
) -> None:
    state = InstrumentStateSnapshot(instrument_id="source-a")
    receipt = InstrumentConfiguredDefaultsApplyReceipt(
        session_id="session-1",
        operation_id="defaults.apply-1",
        instrument_id="source-a",
        config_entry_id="baseline",
        status=status,
        state=state,
    )

    assert (
        InstrumentConfiguredDefaultsApplyReceipt.model_validate_json(
            receipt.model_dump_json()
        )
        == receipt
    )
    with pytest.raises(ValidationError, match="requires synchronized state"):
        InstrumentConfiguredDefaultsApplyReceipt(
            **receipt.model_dump(exclude={"state"})
        )
    with pytest.raises(ValidationError, match="cannot contain problems"):
        InstrumentConfiguredDefaultsApplyReceipt(
            **receipt.model_dump(exclude={"problems"}),
            problems=(_configured_defaults_problem(),),
        )
    with pytest.raises(ValidationError, match="must match instrument_id"):
        InstrumentConfiguredDefaultsApplyReceipt(
            **receipt.model_dump(exclude={"state"}),
            state=state.model_copy(update={"instrument_id": "source-b"}),
        )


def test_rejected_configured_defaults_apply_has_problem_without_state() -> None:
    receipt = InstrumentConfiguredDefaultsApplyReceipt(
        session_id="session-1",
        operation_id="defaults.apply-1",
        instrument_id="source-a",
        config_entry_id="baseline",
        status="rejected",
        problems=(_configured_defaults_problem(),),
    )

    assert (
        InstrumentConfiguredDefaultsApplyReceipt.model_validate_json(
            receipt.model_dump_json()
        )
        == receipt
    )
    with pytest.raises(ValidationError, match="requires a problem"):
        InstrumentConfiguredDefaultsApplyReceipt(
            **receipt.model_dump(exclude={"problems"})
        )
    with pytest.raises(ValidationError, match="cannot report state"):
        InstrumentConfiguredDefaultsApplyReceipt(
            **receipt.model_dump(exclude={"state"}),
            state=InstrumentStateSnapshot(instrument_id="source-a"),
        )


def _configured_defaults_problem() -> Problem:
    return Problem(
        code="configured_defaults_rejected",
        phase=ProblemPhase.EXECUTION,
        message="configured defaults were rejected",
    )
