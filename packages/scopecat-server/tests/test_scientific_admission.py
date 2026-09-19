"""Scientific evidence is checked once and published with the run admission."""

import sqlite3
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.control.models import RunPlanSummary
from scopecat.daemon.wire import RunSubmission, SampleCreateCommand
from scopecat.project import load_project
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.run import AnalysisCandidateRunConfigSource
from scopecat.records.run_request import RunRequest
from scopecat.records.sample import SampleRevisionDraft, SampleSelector
from scopecat.records.scientific_binding import RegisteredTargetSubject
from scopecat.records.scientific_scope import MeasurementTarget, TargetMember
from scopecat.records.target_catalog import (
    TargetCreateCommand,
    TargetReviseCommand,
    TargetRevision,
    TargetRevisionDraft,
)
from scopecat.runs.refs import SCIENTIFIC_BINDING_REF
from scopecat_testkit.config_registry import load_config

from scopecat_server import BackendConflict, LocalDaemonRuntime
from scopecat_server.snapshots import create_snapshot, restore_snapshot
from scopecat_server.storage.sqlite.run_repository import (
    PreparedRunSkeleton,
    SQLiteRunRepository,
)


def _target(
    runtime: LocalDaemonRuntime, config: ConfigProfileSnapshot
) -> TargetRevision:
    sample = runtime.application.samples.create(
        SampleCreateCommand(
            operation_id="create-chip",
            sample_id="chip",
            kind="chip",
            actor="test",
            content=SampleRevisionDraft(
                display_name="Chip", topology=config.system.topology
            ),
        )
    ).revision
    return runtime.application.targets.create(
        TargetCreateCommand(
            catalog_id=runtime.application.project_id,
            target_id="target",
            draft=TargetRevisionDraft(
                name="First",
                actor="test",
                content=MeasurementTarget(
                    members=(
                        TargetMember(
                            id="A",
                            sample_id=sample.sample_id,
                            revision=sample.revision,
                            content_hash=sample.content_hash,
                        ),
                    ),
                ),
            ),
        )
    )


def _submission(
    runtime: LocalDaemonRuntime,
    config: ConfigProfileSnapshot,
    target: TargetRevision,
    key: str = "target-run",
) -> RunSubmission:
    samples = runtime.application.samples.resolve_bindings(
        (SampleSelector(sample_id="chip", revision=1),)
    )
    binding = bind_scientific_evidence(
        catalog_id=runtime.application.project_id,
        config=config,
        samples=samples,
        target=target,
        sample_revisions={("chip", 1): runtime.application.samples.revision("chip", 1)},
    )
    return RunSubmission(
        submission_id=key,
        config=config,
        scientific_binding=binding,
        request=RunRequest(experiment_id="target", samples=binding.sample_selectors()),
        plan=RunPlanSummary(
            experiment_id="target",
            experiment_kind="target",
            point_plan_fingerprint="a" * 64,
            measurement_contract_fingerprint="b" * 64,
            point_count=1,
            initial_point_count=1,
            point_limit=1,
        ),
    )


def _counts(root: Path) -> tuple[int, ...]:
    with sqlite3.connect(root / ".scopecat/control.sqlite3") as database:
        return tuple(
            cast(
                "tuple[int]",
                database.execute(f"SELECT COUNT(*) FROM {table}").fetchone(),  # noqa: S608 - fixed table inventory
            )[0]
            for table in (
                "scheduler_runs",
                "runs",
                "run_addresses",
                "run_sample_bindings",
                "run_repository_refs",
            )
        )


def test_target_binding_retains_exact_revision_retry_and_restore(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    manifest = source / "scopecat.toml"
    manifest.write_text("[lab]\n")
    config = load_config()
    with LocalDaemonRuntime(source, bootstrap_config=config) as runtime:
        first = _target(runtime, config)
        request = _submission(runtime, config, first)
        second = runtime.application.targets.revise(
            TargetReviseCommand(
                expected=first.ref,
                draft=TargetRevisionDraft(
                    name="Renamed", actor="test", content=first.content
                ),
            )
        )
        admitted = runtime.application.submit_run(request)
        assert admitted.snapshot.scientific_binding == request.scientific_binding
        assert isinstance(
            admitted.snapshot.scientific_binding.subject, RegisteredTargetSubject
        )
        assert admitted.snapshot.scientific_binding.subject.ref == first.ref
        original = runtime.application.runs.get_run(admitted.run_id)
        assert original.address is not None
        candidate_source = AnalysisCandidateRunConfigSource(
            source_run_id=admitted.run_id,
            analysis_record_id="fit",
            proposal_id="proposal",
            base_config_content_hash=request.scientific_binding.config_content_hash,
            content_hash=request.scientific_binding.config_content_hash,
        )
        candidate_scope = runtime.application._admission._require_candidate_subject
        candidate_scope(candidate_source, request.scientific_binding)
        inline = bind_scientific_evidence(
            catalog_id=runtime.application.project_id,
            config=config,
            samples=request.scientific_binding.samples,
            sample_revisions={},
        )
        with pytest.raises(BackendConflict, match="original scientific subject"):
            candidate_scope(candidate_source, inline)
        assert runtime.application.submit_run(request) == admitted
        with pytest.raises(BackendConflict, match="different content"):
            runtime.application.submit_run(_submission(runtime, config, second))
        assert (
            runtime.application.runs.get_run(admitted.run_id).address
            == original.address
        )
        with sqlite3.connect(source / ".scopecat/control.sqlite3") as database:
            assert database.execute(
                "SELECT COUNT(*) FROM run_repository_refs WHERE run_id=? AND ref=?",
                (admitted.run_id, SCIENTIFIC_BINDING_REF),
            ).fetchone() == (1,)
    create_snapshot(load_project(manifest), tmp_path / "snapshot")
    restore_snapshot(tmp_path / "snapshot", tmp_path / "restored")
    with LocalDaemonRuntime(tmp_path / "restored") as restored:
        view = restored.application.runs.get_run(admitted.run_id)
        assert view.snapshot.scientific_binding == request.scientific_binding
        assert view.address == original.address


def test_foreign_binding_and_mismatched_projection_allocate_nothing(
    tmp_path: Path,
) -> None:
    config = load_config()
    with LocalDaemonRuntime(tmp_path, bootstrap_config=config) as runtime:
        target = _target(runtime, config)
        request = _submission(runtime, config, target)
        subject = request.scientific_binding.subject
        assert isinstance(subject, RegisteredTargetSubject)
        before = _counts(tmp_path)
        foreign = subject.model_copy(
            update={"ref": subject.ref.model_copy(update={"catalog_id": "elsewhere"})}
        )
        for malformed, message in (
            (foreign, "another catalog"),
            (subject.model_copy(update={"projection": ()}), "retained evidence"),
        ):
            changed = request.model_copy(
                update={
                    "scientific_binding": request.scientific_binding.model_copy(
                        update={"subject": malformed}
                    )
                }
            )
            with pytest.raises(BackendConflict, match=message):
                runtime.application.submit_run(changed)
            assert _counts(tmp_path) == before


def test_scientific_ref_failure_rolls_back_admission_and_address(
    tmp_path: Path,
) -> None:
    config = load_config()
    with LocalDaemonRuntime(tmp_path, bootstrap_config=config) as runtime:
        request = _submission(runtime, config, _target(runtime, config))
        before = _counts(tmp_path)
        original = SQLiteRunRepository.commit_run_skeleton_in_transaction

        def fail_after_refs(
            self: SQLiteRunRepository,
            connection: sqlite3.Connection,
            prepared: PreparedRunSkeleton,
        ) -> None:
            original(self, connection, prepared)
            raise RuntimeError("injected scientific publication failure")

        with (
            patch.object(
                SQLiteRunRepository,
                "commit_run_skeleton_in_transaction",
                fail_after_refs,
            ),
            pytest.raises(RuntimeError, match="injected scientific"),
        ):
            runtime.application.submit_run(request)
        assert _counts(tmp_path) == before
        accepted = runtime.application.submit_run(request)
        assert accepted.snapshot.scientific_binding == request.scientific_binding
        assert runtime.application.runs.get_run(accepted.run_id).address is not None


@pytest.mark.parametrize("mode", ["fixed", "generic", "wrong-subject", "wrong-step"])
def test_every_durable_child_checks_step_and_declared_parent_binding(
    tmp_path: Path, mode: str
) -> None:
    from scopecat.automation import (
        ProcedureDefinitionRef,
        ProcedureStepBeginCommand,
        ProcedureSubmitCommand,
        ProcedureWorkerLeaseAcquireCommand,
        procedure_step_operation_id,
    )
    from scopecat.records.plan_ref import ProcedureChildSubmission

    config = load_config()
    with LocalDaemonRuntime(tmp_path, bootstrap_config=config) as runtime:
        target = _target(runtime, config)
        original = _submission(runtime, config, target)
        changed = runtime.application.targets.revise(
            TargetReviseCommand(
                expected=target.ref,
                draft=TargetRevisionDraft(
                    name="Next", actor="test", content=target.content
                ),
            )
        )
        command = ProcedureSubmitCommand(
            request_key="scoped-parent",
            definition=ProcedureDefinitionRef(
                id="author", version="1", fingerprint="sha256:" + "a" * 64
            ),
            intent={},
            samples=original.scientific_binding.sample_selectors(),
            scientific_binding=None
            if mode == "generic"
            else original.scientific_binding,
        )
        service = runtime.application.automation
        parent = service.submit(command).run
        assert service.submit(command).run == parent
        assert parent.scientific_binding == command.scientific_binding
        with pytest.raises(BackendConflict, match="different intent"):
            service.submit(
                command.model_copy(
                    update={
                        "scientific_binding": _submission(
                            runtime, config, changed
                        ).scientific_binding
                    }
                )
            )
        acquired = service.acquire_lease(
            ProcedureWorkerLeaseAcquireCommand(
                procedure_run_id=parent.procedure_run_id,
                worker_id="worker",
                expected_run_revision=parent.revision,
            )
        )
        child = (
            _submission(runtime, config, changed)
            if mode == "wrong-subject"
            else original
        ).model_copy(
            update={
                "submission_id": procedure_step_operation_id(
                    parent.procedure_run_id, "measure"
                ),
                "procedure_child": ProcedureChildSubmission(
                    procedure_run_id=parent.procedure_run_id, step_key="measure"
                ),
            }
        )
        service.begin_step(
            ProcedureStepBeginCommand(
                procedure_run_id=parent.procedure_run_id,
                lease_token=acquired.lease.lease_token,
                expected_run_revision=acquired.run.revision,
                step_key="measure",
                operation="run",
                intent_hash="sha256:"
                + ("f" * 64 if mode == "wrong-step" else child.intent_content_hash),
            )
        )
        before = _counts(tmp_path)
        if mode.startswith("wrong"):
            with pytest.raises(BackendConflict, match="live durable parent step"):
                runtime.application.submit_run(child)
            assert _counts(tmp_path) == before
        else:
            admitted = runtime.application.submit_run(child)
            assert admitted.snapshot.scientific_binding == original.scientific_binding
            assert runtime.application.submit_run(child).run_id == admitted.run_id


def test_saved_plan_reuses_authoritative_target_validation(tmp_path: Path) -> None:
    from scopecat.records.experiment_plan import (
        ExperimentPlanDefinition,
        ExperimentPlanSave,
    )
    from scopecat.records.plan_ref import PlanConfigRef
    from scopecat.records.scientific_selection import (
        RegisteredTargetChoice,
        SavedConfiguration,
        ScientificSelection,
    )

    config = load_config()
    with LocalDaemonRuntime(tmp_path, bootstrap_config=config) as runtime:
        target = _target(runtime, config)
        original = _submission(runtime, config, target)
        active = runtime.application.config.get_active_config()
        definition = ExperimentPlanDefinition(
            experiment="author",
            version="1",
            definition_hash="sha256:" + "a" * 64,
            selection=ScientificSelection(
                subject=RegisteredTargetChoice(ref=target.ref),
                configuration=SavedConfiguration(
                    ref=PlanConfigRef(
                        entry_id=active.entry.id, content_hash=active.entry.content_hash
                    )
                ),
            ),
            scientific_binding=original.scientific_binding,
        )
        runtime.application.targets.revise(
            TargetReviseCommand(
                expected=target.ref,
                draft=TargetRevisionDraft(
                    name="Next", actor="test", content=target.content
                ),
            )
        )
        saved = runtime.application.plans.save(
            ExperimentPlanSave(
                name="Exact target", saved_by="test", definition=definition
            )
        )
        assert saved.definition.scientific_binding == original.scientific_binding
        forged = original.scientific_binding.model_copy(
            update={"setup_content_hash": "sha256:" + "f" * 64}
        )
        with pytest.raises(BackendConflict, match="retained evidence"):
            runtime.application.plans.save(
                ExperimentPlanSave(
                    name="Forged",
                    saved_by="test",
                    definition=definition.model_copy(
                        update={"scientific_binding": forged}
                    ),
                )
            )
        assert runtime.application.plans.repository.list().items == (saved,)
