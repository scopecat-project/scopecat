"""Scientific evidence is checked once and published with the run admission."""

import sqlite3
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.control.models import RunPlanSummary
from scopecat.daemon.wire import (
    ParameterResolveCommand,
    RunSubmission,
    SampleCreateCommand,
)
from scopecat.project import load_project
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.configuration_fence import SetupRevisionFence
from scopecat.records.run import AnalysisCandidateRunConfigSource
from scopecat.records.run_request import RunRequest
from scopecat.records.sample import SampleRevisionDraft, SampleSelector
from scopecat.records.scientific_binding import RegisteredTargetSubject
from scopecat.records.scientific_scope import (
    MeasurementTarget,
    TargetMember,
)
from scopecat.records.setup import SetupRevisionRef
from scopecat.records.target_catalog import (
    TargetCreateCommand,
    TargetReviseCommand,
    TargetRevision,
    TargetRevisionDraft,
)
from scopecat.runs.refs import SCIENTIFIC_BINDING_REF
from scopecat_testkit.config_registry import load_config
from scopecat_testkit.server.instruments import signal_endpoint

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
    *,
    setup: SetupRevisionRef | None = None,
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
        execution_setup=setup or runtime.application.setup.resolve("initial").ref,
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
    with LocalDaemonRuntime(
        source, bootstrap_config=config, instrument_endpoint=signal_endpoint()
    ) as runtime:
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
    with LocalDaemonRuntime(
        tmp_path, bootstrap_config=config, instrument_endpoint=signal_endpoint()
    ) as runtime:
        target = _target(runtime, config)
        request = _submission(runtime, config, target)
        subject = request.scientific_binding.subject
        assert isinstance(subject, RegisteredTargetSubject)
        before = _counts(tmp_path)
        foreign = subject.model_copy(
            update={"ref": subject.ref.model_copy(update={"catalog_id": "elsewhere"})}
        )
        mapping = request.scientific_binding.target_binding
        assert mapping is not None
        for malformed, message in (
            (
                request.scientific_binding.model_copy(update={"subject": foreign}),
                "another catalog",
            ),
            (
                request.scientific_binding.model_copy(
                    update={
                        "target_binding": mapping.model_copy(update={"entities": ()})
                    }
                ),
                "retained evidence",
            ),
        ):
            changed = request.model_copy(update={"scientific_binding": malformed})
            with pytest.raises(BackendConflict, match=message):
                runtime.application.submit_run(changed)
            assert _counts(tmp_path) == before


def test_scientific_ref_failure_rolls_back_admission_and_address(
    tmp_path: Path,
) -> None:
    config = load_config()
    with LocalDaemonRuntime(
        tmp_path, bootstrap_config=config, instrument_endpoint=signal_endpoint()
    ) as runtime:
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


@pytest.mark.parametrize(
    "mode", ["fixed", "generic", "parameter-change", "wrong-subject", "wrong-step"]
)
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
    with LocalDaemonRuntime(
        tmp_path, bootstrap_config=config, instrument_endpoint=signal_endpoint()
    ) as runtime:
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
            expected_configuration=SetupRevisionFence(
                revision=original.execution_setup
            ),
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
        child_config = config
        if mode == "parameter-change":
            from scopecat.config.drafts import ConfigDraft
            from scopecat.kernel.quantity import Quantity

            candidate = (
                ConfigDraft(config)
                .replace_scalar("drive_frequency", Quantity(value=5.1, unit="GHz"))
                .check()
                .candidate
            )
            assert candidate is not None
            child_config = candidate
        child = _submission(
            runtime, child_config, changed if mode == "wrong-subject" else target
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
            assert admitted.snapshot.scientific_binding == child.scientific_binding
            assert (
                admitted.snapshot.scientific_binding.subject
                == original.scientific_binding.subject
            )
            if mode == "parameter-change":
                assert (
                    admitted.snapshot.scientific_binding.config_content_hash
                    != original.scientific_binding.config_content_hash
                )
            assert runtime.application.submit_run(child).run_id == admitted.run_id


def test_saved_plan_reuses_authoritative_target_validation(tmp_path: Path) -> None:
    from scopecat.records.experiment_plan import (
        ExperimentPlanDefinition,
        ExperimentPlanSave,
    )
    from scopecat.records.scientific_selection import (
        ParameterConfiguration,
        RegisteredTargetChoice,
        ScientificSelection,
    )

    config = load_config()
    with LocalDaemonRuntime(
        tmp_path, bootstrap_config=config, instrument_endpoint=signal_endpoint()
    ) as runtime:
        target = _target(runtime, config)
        original = _submission(runtime, config, target)
        parameters = runtime.application.config.parameter_revision(config.id)
        setup = runtime.application.setup.resolve("initial")
        resolved = runtime.application.config.resolve_parameters(
            ParameterResolveCommand(parameters=parameters.ref, setup=setup.ref)
        )
        config = resolved.config
        original = _submission(runtime, config, target)
        definition = ExperimentPlanDefinition(
            workspace_id="test-source",
            experiment="author",
            version="1",
            definition_hash="sha256:" + "a" * 64,
            selection=ScientificSelection(
                subject=RegisteredTargetChoice(ref=target.ref),
                configuration=ParameterConfiguration(
                    ref=parameters.ref, setup=setup.ref
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


@pytest.mark.parametrize("drop_subject", [False, True])
def test_generic_saved_plan_allows_distinct_stage_configurations(
    tmp_path: Path,
    drop_subject: bool,
) -> None:
    from scopecat.application.experiment_plans import plan_launch_request
    from scopecat.automation import (
        ProcedureDefinitionRef,
        ProcedureStepBeginCommand,
        ProcedureSubmitCommand,
        ProcedureWorkerLeaseAcquireCommand,
        procedure_step_operation_id,
    )
    from scopecat.kernel.content_identity import sha256_json_hash
    from scopecat.records.experiment_plan import (
        ExperimentPlanDefinition,
        ExperimentPlanSave,
    )
    from scopecat.records.launch_request import LaunchRequest
    from scopecat.records.manual_preview import ManualPreviewBinding
    from scopecat.records.plan_ref import ProcedureChildSubmission
    from scopecat.records.scientific_selection import (
        ParameterConfiguration,
        ReviewedScientificSelection,
        SampleSubjectChoice,
        ScientificSelection,
    )

    config = load_config()
    with LocalDaemonRuntime(
        tmp_path, bootstrap_config=config, instrument_endpoint=signal_endpoint()
    ) as runtime:
        application = runtime.application
        parameters = application.config.parameter_revision(config.id)
        setup = application.setup.resolve("initial")
        resolved = application.config.resolve_parameters(
            ParameterResolveCommand(parameters=parameters.ref, setup=setup.ref)
        )
        config = resolved.config
        _target(runtime, config)
        samples = application.samples.resolve_bindings(
            (SampleSelector(sample_id="chip", revision=1),)
        )
        binding = bind_scientific_evidence(
            catalog_id=application.project_id,
            config=config,
            samples=samples,
            sample_revisions={},
        )
        saved = application.plans.save(
            ExperimentPlanSave(
                name="Multiple stages",
                saved_by="test",
                definition=ExperimentPlanDefinition(
                    workspace_id="test-source",
                    experiment="stages",
                    version="1",
                    definition_hash="sha256:" + "a" * 64,
                    selection=ScientificSelection(
                        subject=SampleSubjectChoice(sample_id="chip", revision=1),
                        configuration=ParameterConfiguration(
                            ref=parameters.ref, setup=setup.ref
                        ),
                    ),
                    scientific_binding=binding,
                ),
            )
        )
        reviewed = ReviewedScientificSelection(
            binding=binding,
            config_source=resolved.config_source,
        )
        preview_request = plan_launch_request(saved, actor="test").model_copy(
            update={"reviewed": reviewed}
        )
        fence = application.manual_previews.repository.record(
            cursor=application.manual_previews.cursor(),
            binding=ManualPreviewBinding(
                request_hash=preview_request.request_hash,
                config_source_hash=sha256_json_hash(
                    reviewed.config_source.model_dump(mode="json")
                ),
            ),
            instruments=(),
        )
        request = LaunchRequest.model_validate(
            preview_request.model_dump(mode="json")
            | {
                "action": "submit",
                "request_key": "stages",
                "expected_request_hash": preview_request.request_hash,
                "manual_state": fence.model_dump(mode="json"),
            }
        )
        command = ProcedureSubmitCommand(
            request_key="stages",
            expected_configuration=SetupRevisionFence(revision=setup.ref),
            definition=ProcedureDefinitionRef(
                id="stages", version="1", fingerprint="sha256:" + "b" * 64
            ),
            intent={
                "actor": "test",
                "record_collection": None,
                "request_hash": request.request_hash,
            },
            plan_ref=saved.ref,
            samples=binding.sample_selectors(),
            plan_request=request,
            expected_manual_preview=fence,
        )
        service = application.automation
        parent = service.submit(command).run
        assert parent.scientific_binding is None
        assert parent.resolved_samples == binding.sample_selectors()
        assert service.submit(command).run == parent
        changed_config = config.model_copy(update={"id": "stage-two"})
        changed_binding = bind_scientific_evidence(
            catalog_id=application.project_id,
            config=changed_config,
            samples=samples,
            sample_revisions={},
        )
        assert changed_binding != binding
        with pytest.raises(BackendConflict, match="immutable plan"):
            service.submit(
                command.model_copy(
                    update={
                        "request_key": "wrong-fixed",
                        "scientific_binding": changed_binding,
                    }
                )
            )
        with pytest.raises(BackendConflict, match="immutable plan"):
            service.submit(
                command.model_copy(
                    update={
                        "request_key": "wrong-samples",
                        "samples": (SampleSelector(sample_id="unrelated", revision=1),),
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
        child = RunSubmission(
            execution_setup=runtime.application.setup.resolve("initial").ref,
            submission_id=procedure_step_operation_id(
                parent.procedure_run_id, "second"
            ),
            config=changed_config,
            scientific_binding=changed_binding,
            request=RunRequest(
                experiment_id="stage",
                plan_ref=saved.ref,
                samples=binding.sample_selectors(),
            ),
            plan=RunPlanSummary(
                experiment_id="stage",
                experiment_kind="stage",
                point_plan_fingerprint="a" * 64,
                measurement_contract_fingerprint="b" * 64,
                point_count=1,
                initial_point_count=1,
                point_limit=1,
            ),
            procedure_child=ProcedureChildSubmission(
                procedure_run_id=parent.procedure_run_id, step_key="second"
            ),
        )
        if drop_subject:
            child = child.model_copy(
                update={
                    "request": child.request.model_copy(update={"samples": ()}),
                    "scientific_binding": bind_scientific_evidence(
                        catalog_id=application.project_id,
                        config=changed_config,
                        samples=(),
                        sample_revisions={},
                    ),
                }
            )
        service.begin_step(
            ProcedureStepBeginCommand(
                procedure_run_id=parent.procedure_run_id,
                lease_token=acquired.lease.lease_token,
                expected_run_revision=acquired.run.revision,
                step_key="second",
                operation="run",
                intent_hash="sha256:" + child.intent_content_hash,
            )
        )
        before = _counts(tmp_path)
        if drop_subject:
            with pytest.raises(BackendConflict, match="live durable parent step"):
                application.submit_run(child)
            assert _counts(tmp_path) == before
        else:
            admitted = application.submit_run(child)
            assert admitted.snapshot.scientific_binding == changed_binding


def test_independent_setup_contexts_admit_without_global_selection(
    tmp_path: Path,
) -> None:
    from datetime import timedelta

    from scopecat.automation import ProcedureDefinitionRef, ProcedureSubmitCommand
    from scopecat.control.models import RunResourceRequirement
    from scopecat.daemon.wire import (
        ParameterResolveCommand,
        ParameterSaveCommand,
        SetupImportCommand,
    )
    from scopecat.records.configuration_fence import SetupRevisionFence
    from scopecat.records.setup import ExecutableSetupSnapshot

    from scopecat_server.storage.sqlite.control_plane import RunResourcesBusy

    config = load_config()
    with LocalDaemonRuntime(tmp_path, instrument_endpoint=signal_endpoint()) as runtime:
        application = runtime.application
        target = _target(runtime, config)
        parameters = application.config.save_parameters(
            ParameterSaveCommand(
                revision_id="shared-parameters",
                catalog=config.parameter_catalog,
                parameters=config.parameter_snapshot,
                actor="author",
            )
        )
        setup = ExecutableSetupSnapshot.from_config(config)
        assert setup.domain_target is not None
        alternate = setup.model_copy(
            update={
                "domain_target": setup.domain_target.model_copy(
                    update={"id": "alternate-context"}
                ),
                "instrument_registry": setup.instrument_registry.model_copy(
                    update={
                        "instruments": [
                            setup.instrument_registry.instruments[0].model_copy(
                                update={"id": "second-name"}
                            )
                        ],
                    }
                ),
                "routing": setup.routing.model_copy(
                    update={
                        "routes": [
                            route.model_copy(update={"instrument_id": "second-name"})
                            for route in setup.routing.routes
                        ],
                    }
                ),
            }
        )
        claims = []
        run_ids: list[str] = []
        for name, selected in (("first", setup), ("second", alternate)):
            revision = application.setup.import_recipe(
                SetupImportCommand(
                    revision_id=name,
                    setup=selected,
                    actor="maintainer",
                )
            )
            resolved = application.config.resolve_parameters(
                ParameterResolveCommand(
                    parameters=parameters.ref,
                    setup=revision.ref,
                )
            )
            child = _submission(
                runtime, resolved.config, target, key=name, setup=revision.ref
            )
            instrument = selected.instrument_registry.instruments[0]
            child = child.model_copy(
                update={
                    "config_source": resolved.config_source,
                    "plan": child.plan.model_copy(
                        update={
                            "run_resource_requirements": (
                                RunResourceRequirement(
                                    kind="instrument", id=instrument.id
                                ),
                            )
                        }
                    ),
                }
            )
            parent = ProcedureSubmitCommand(
                request_key=name,
                definition=ProcedureDefinitionRef(
                    id="author", version="1", fingerprint="sha256:" + "a" * 64
                ),
                intent={},
                samples=child.scientific_binding.sample_selectors(),
                scientific_binding=child.scientific_binding,
                expected_configuration=SetupRevisionFence(revision=revision.ref),
            )
            application.automation.submit(parent)
            admitted = application.submit_run(child)
            run_ids.append(admitted.run_id)
            retained = application._admission._control.get_run(admitted.run_id)
            claims.append(retained.admission.resource_claims)
            assert application.submit_run(child).run_id == admitted.run_id
            wrong = parent.model_copy(
                update={
                    "request_key": name + "-wrong",
                    "expected_configuration": SetupRevisionFence(
                        revision=revision.ref.model_copy(
                            update={"content_hash": "sha256:" + "0" * 64}
                        )
                    ),
                }
            )
            with pytest.raises(BackendConflict, match="reference changed"):
                application.automation.submit(wrong)
        assert claims[0] == claims[1]
        control = application._admission._control
        with control.write_transaction() as connection:
            control.start_execution_in_transaction(
                connection, run_ids[0], executor_id="first", ttl=timedelta(minutes=1)
            )
        with pytest.raises(RunResourcesBusy), control.write_transaction() as connection:
            control.start_execution_in_transaction(
                connection, run_ids[1], executor_id="second", ttl=timedelta(minutes=1)
            )
        assert len(application.setup.definitions()) == 2


def test_procedure_setup_is_explicit_and_independent_of_other_definitions(
    tmp_path: Path,
) -> None:
    from scopecat.automation import ProcedureDefinitionRef, ProcedureSubmitCommand
    from scopecat.daemon.wire import SetupImportCommand
    from scopecat.records.configuration_fence import SetupRevisionFence
    from scopecat.records.setup import ExecutableSetupSnapshot

    config = load_config()
    with LocalDaemonRuntime(
        tmp_path, bootstrap_config=config, instrument_endpoint=signal_endpoint()
    ) as runtime:
        target = _target(runtime, config)
        child = _submission(runtime, config, target)
        command = ProcedureSubmitCommand(
            request_key="exact",
            definition=ProcedureDefinitionRef(
                id="author", version="1", fingerprint="sha256:" + "a" * 64
            ),
            intent={},
            samples=child.scientific_binding.sample_selectors(),
            scientific_binding=child.scientific_binding,
            expected_configuration=SetupRevisionFence(revision=child.execution_setup),
        )
        service = runtime.application.automation
        parent = service.submit(command).run
        admitted = runtime.application.submit_run(child)
        changed = ExecutableSetupSnapshot.from_config(config)
        assert changed.domain_target is not None
        other = runtime.application.setup.import_recipe(
            SetupImportCommand(
                revision_id="other",
                actor="test",
                setup=changed.model_copy(
                    update={
                        "domain_target": changed.domain_target.model_copy(
                            update={"id": "other"}
                        )
                    }
                ),
            )
        )
        assert service.submit(command).run == parent
        assert runtime.application.submit_run(child).run_id == admitted.run_id
        assert (
            service.submit(
                command.model_copy(update={"request_key": "another-exact"})
            ).run.scientific_binding
            == child.scientific_binding
        )
        before = _counts(tmp_path)
        for fence in (None, SetupRevisionFence(revision=other.ref)):
            with pytest.raises(
                BackendConflict, match=r"exact execution setup|executable setup differs"
            ):
                service.submit(
                    command.model_copy(
                        update={"request_key": "wrong", "expected_configuration": fence}
                    )
                )
        assert _counts(tmp_path) == before
