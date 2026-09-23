"""A notebook selection keeps target evidence through real saved-plan execution."""

from pathlib import Path
from uuid import uuid4

from scopecat.api.lab import LabClient
from scopecat.application.author_project import AuthorProject
from scopecat.daemon.client import DaemonClient
from scopecat.records.parameter_revision import ParameterRevision
from scopecat.records.sample import SampleRevisionDraft
from scopecat.records.scientific_scope import MeasurementTarget, TargetMember
from scopecat.records.target_catalog import (
    TargetCreateCommand,
    TargetReviseCommand,
    TargetRevisionDraft,
)


def test_target_plan_keeps_exact_reviewed_binding_after_catalog_and_session_changes(
    tmp_path: Path,
    independent_lab_daemon: str,
    independent_parameters: ParameterRevision,
):
    endpoint = independent_lab_daemon
    key = uuid4().hex
    with (
        AuthorProject(endpoint, receipts=tmp_path) as session,
        LabClient(DaemonClient(endpoint)) as lab,
    ):
        setup = lab.setup.active()
        sample = lab.samples.create(
            f"target-{key}",
            kind="synthetic",
            content=SampleRevisionDraft(
                display_name="Target chip",
                topology=setup.revision.setup.topology,
            ),
        )
        revision = lab.samples.revision(sample.id, 1)
        target = lab.create_target(
            TargetCreateCommand(
                catalog_id=lab.health().project_id,
                target_id=f"target-{key}",
                draft=TargetRevisionDraft(
                    name="Original target",
                    actor="alice",
                    content=MeasurementTarget(
                        members=(
                            TargetMember(
                                id="chip",
                                sample_id=sample.id,
                                revision=1,
                                content_hash=revision.content_hash,
                            ),
                        )
                    ),
                ),
            )
        )
        session.use(
            target=target.ref.target_id,
            parameters=independent_parameters.ref,
            setup=setup.revision.ref,
        )
        prepared = session.prepare("signal")
        binding = prepared.preview.reviewed.binding
        assert binding.subject.kind == "registered_target"
        assert binding.subject.ref == target.ref
        plan = prepared.save_plan("Exact target", saved_by="alice")
        lab.revise_target(
            TargetReviseCommand(
                expected=target.ref,
                draft=TargetRevisionDraft(
                    name="New target label",
                    actor="bob",
                    content=target.content,
                ),
            )
        )
        session.refresh()
        assert session.selection.science.subject.kind == "registered_target"
        assert session.selection.science.subject.ref == target.ref
        session.use(sample=sample.id)
        reopened = session.prepare_plan(plan.ref)
        assert reopened.preview.reviewed.binding == binding
        job = reopened.run()
        run = job.wait(timeout=60).result()
        assert run.snapshot.scientific_binding == binding
        parent = job.recover()
        assert parent is not None
        assert parent.scientific_binding == binding
        assert run.samples == binding.samples
        assert lab.setup.active() == setup
        assert lab.config.registry().entries == ()
