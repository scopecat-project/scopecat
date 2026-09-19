# pyright: reportUnknownLambdaType=false
"""The public selection resolves once and survives moving mutable heads."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from scopecat_testkit.workflow_fixtures import load_config

from scopecat.api.lab import LabClient
from scopecat.application.launch_config import resolve_launch_config
from scopecat.records.config import config_content_hash
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.run import ConfigRegistryRunConfigSource
from scopecat.records.sample import (
    SampleRecord,
    SampleRevision,
    SampleRevisionDraft,
    sample_revision_content_hash,
)
from scopecat.records.scientific_scope import MeasurementTarget, TargetMember
from scopecat.records.scientific_selection import (
    RegisteredTargetChoice,
    SampleSubjectChoice,
    ScientificSelection,
)
from scopecat.records.target_catalog import TargetRevision, TargetRevisionRef


def _lab() -> tuple[LabClient, dict[str, object], TargetRevision]:
    config = load_config()
    content = SampleRevisionDraft(display_name="chip", topology=config.system.topology)
    sample = SampleRevision(
        sample_id="chip",
        revision=1,
        actor="test",
        content=content,
        content_hash=sample_revision_content_hash(sample_id="chip", content=content),
    )
    target_content = MeasurementTarget(
        members=(
            TargetMember(
                id="A", sample_id="chip", revision=1, content_hash=sample.content_hash
            ),
        )
    )
    target = TargetRevision(
        ref=TargetRevisionRef(
            catalog_id="catalog",
            target_id="target",
            revision=1,
            content_hash=target_content.content_hash,
        ),
        name="Target",
        content=target_content,
        actor="test",
        recorded_at=datetime.now(UTC),
    )
    source = ConfigRegistryRunConfigSource(
        selector="active",
        entry_id="config",
        config_ref="config",
        content_hash=config_content_hash(config),
        registry_generation=1,
    )
    state: dict[str, object] = {"head": sample, "exact_reads": []}

    def sample_revision(sample_id: str, revision: int) -> SampleRevision:
        assert (sample_id, revision) == ("chip", 1)
        cast("list[int]", state["exact_reads"]).append(revision)
        return sample

    client = SimpleNamespace(
        get_sample=lambda _: SimpleNamespace(
            record=SampleRecord(id="chip", kind="chip", active_revision=1),
            revision=state["head"],
        ),
        sample_revision=sample_revision,
    )
    operations = SimpleNamespace(
        client=client,
        resolve_with_source=lambda _: (config, source),
        active=lambda: SimpleNamespace(activation=SimpleNamespace(generation=1)),
        entry=lambda _: SimpleNamespace(
            config=config,
            entry=SimpleNamespace(
                id="config",
                config_ref="config",
                content_hash=config_content_hash(config),
            ),
        ),
    )
    lab = cast(
        "LabClient",
        cast(
            "object",
            SimpleNamespace(
                config=operations,
                health=lambda: SimpleNamespace(project_id="catalog"),
                resolve_target=lambda ref: (
                    target
                    if ref == target.ref
                    else pytest.fail("unexpected target ref")
                ),
            ),
        ),
    )
    return lab, state, target


def test_preview_freezes_registered_target_and_hash_rejects_binding_swap() -> None:
    lab, state, target = _lab()
    request = LaunchRequest(
        action="preview",
        experiment="signal",
        version="1",
        selection=ScientificSelection(subject=RegisteredTargetChoice(ref=target.ref)),
    )
    resolved = resolve_launch_config(lab, request)
    frozen = request.model_copy(update={"reviewed": resolved.reviewed})
    binding = resolved.reviewed.binding
    assert binding.subject.kind == "registered_target"
    assert binding.subject.projection[0].target_entity.member_id == "A"
    assert binding.samples[0].role == "subject"
    state["head"] = cast("SampleRevision", state["head"]).model_copy(
        update={"revision": 2}
    )
    submission = LaunchRequest.model_validate(
        {
            **frozen.model_dump(),
            "action": "submit",
            "request_key": "once",
            "expected_request_hash": frozen.request_hash,
        }
    )
    assert resolve_launch_config(lab, submission).reviewed.binding == binding
    changed = resolved.reviewed.model_copy(
        update={
            "binding": binding.model_copy(
                update={"setup_content_hash": "sha256:" + "a" * 64}
            )
        }
    )
    with pytest.raises(ValueError, match="request changed"):
        LaunchRequest.model_validate({**submission.model_dump(), "reviewed": changed})


def test_inline_sample_preview_does_not_advance_when_submitted() -> None:
    lab, state, _ = _lab()
    request = LaunchRequest(
        action="preview",
        experiment="signal",
        version="1",
        selection=ScientificSelection(subject=SampleSubjectChoice(sample_id="chip")),
    )
    resolved = resolve_launch_config(lab, request)
    assert resolved.reviewed.binding.samples[0].revision == 1
    state["head"] = cast("SampleRevision", state["head"]).model_copy(
        update={"revision": 2}
    )
    frozen = request.model_copy(update={"reviewed": resolved.reviewed})
    assert (
        resolve_launch_config(lab, frozen).reviewed.binding == resolved.reviewed.binding
    )
    assert state["exact_reads"] == [1]


def test_session_target_selection_is_atomic_and_pins_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scopecat.application.author_project as author_module
    from scopecat.application.author_project import AuthorProject

    _, _, target = _lab()
    head = target
    accepted: list[ScientificSelection] = []

    def resolve(_lab: LabClient, request: LaunchRequest) -> None:
        if isinstance(request.selection.subject, SampleSubjectChoice):
            raise ValueError("sample unavailable")
        accepted.append(request.selection)

    monkeypatch.setattr(author_module, "resolve_launch_config", resolve)

    def current_target(_self: AuthorProject, _name: str) -> TargetRevision:
        return head

    monkeypatch.setattr(AuthorProject, "target", current_target)
    with AuthorProject("http://test") as session:
        first = session.use(target="target")
        assert first.science.subject == RegisteredTargetChoice(ref=target.ref)
        head = target.model_copy(
            update={"ref": target.ref.model_copy(update={"revision": 2})}
        )
        assert session.use(operator="alice").science == first.science
        with pytest.raises(ValueError, match="sample unavailable"):
            session.use(sample="bad")
        assert session.selection.science == first.science
        assert len(accepted) == 1
        assert session.use(target="target").science.subject == RegisteredTargetChoice(
            ref=head.ref
        )


def test_target_member_a_can_use_matching_working_point_and_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scopecat.records.config_context import ConfigContextRef, ContextRunConfigSource
    from scopecat.records.sample import SampleBinding
    from scopecat.records.scientific_scope import DeclaredBatch
    from scopecat.records.scientific_selection import WorkingPointConfiguration

    lab, state, target = _lab()
    sample = cast("SampleRevision", state["head"])
    config = load_config()
    ref = ConfigContextRef(
        entry_id="working-point", content_hash=config_content_hash(config)
    )
    source = ContextRunConfigSource(
        context=ref,
        content_hash=config_content_hash(config),
        lab_generation=1,
        sample=SampleBinding(
            role="subject",
            sample_id="chip",
            revision=1,
            content_hash=sample.content_hash,
            kind="chip",
            display_name="chip",
            context_id="wp",
            batch_id="cooldown",
        ),
    )

    def resolve_context(
        _ref: ConfigContextRef, *, overrides: object
    ) -> SimpleNamespace:
        assert _ref == ref and overrides == ()
        return SimpleNamespace(config=config, config_source=source)

    def batch(batch_id: str) -> None:
        assert batch_id == "cooldown"

    monkeypatch.setattr(lab.config, "resolve_context", resolve_context, raising=False)
    monkeypatch.setattr(lab, "experimental_batch", batch, raising=False)
    selection = ScientificSelection(
        subject=RegisteredTargetChoice(ref=target.ref),
        configuration=WorkingPointConfiguration(ref=ref),
        batch=DeclaredBatch(id="cooldown"),
    )
    request = LaunchRequest(
        action="preview", experiment="signal", version="1", selection=selection
    )
    resolved = resolve_launch_config(lab, request)
    assert resolved.reviewed.binding.samples == (source.sample,)
    assert resolved.reviewed.binding.subject.kind == "registered_target"
    assert resolved.reviewed.binding.subject.content.members[0].id == "A"
    from scopecat.records.scientific_scope import UnscopedBatch

    with pytest.raises(ValueError, match="subject/batch"):
        resolve_launch_config(
            lab,
            request.model_copy(
                update={
                    "selection": selection.model_copy(update={"batch": UnscopedBatch()})
                }
            ),
        )


def test_saved_target_plan_reopens_retained_binding_with_new_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scopecat.application.experiment_plans import (
        plan_definition,
        plan_launch_request,
    )
    from scopecat.application.launch import LaunchPreview
    from scopecat.records.experiment_plan import ExperimentPlanRevision
    from scopecat.records.plan_ref import ExperimentPlanRef
    from scopecat.records.scientific_selection import SavedConfiguration

    lab, state, target = _lab()
    request = LaunchRequest(
        action="preview",
        experiment="signal",
        version="1",
        selection=ScientificSelection(subject=RegisteredTargetChoice(ref=target.ref)),
    )
    resolved = resolve_launch_config(lab, request)
    frozen = request.model_copy(update={"reviewed": resolved.reviewed})
    preview = LaunchPreview(
        experiment_id="signal",
        request_hash=frozen.request_hash,
        reviewed=resolved.reviewed,
        point_count=1,
        summary="preview",
        definition_hash="sha256:" + "a" * 64,
    )
    definition = plan_definition(request, preview)
    assert isinstance(definition.selection.configuration, SavedConfiguration)
    assert definition.scientific_binding == resolved.reviewed.binding
    plan = ExperimentPlanRevision(
        ref=ExperimentPlanRef(
            plan_id="plan", revision=1, content_hash="sha256:" + "b" * 64
        ),
        name="plan",
        definition=definition,
        saved_by="test",
        saved_at=datetime.now(UTC),
    )

    def read_plan(ref: ExperimentPlanRef) -> ExperimentPlanRevision:
        assert ref == plan.ref
        return plan

    monkeypatch.setattr(lab.config.client, "experiment_plan", read_plan, raising=False)
    state["head"] = cast("SampleRevision", state["head"]).model_copy(
        update={"revision": 2}
    )
    reopened = plan_launch_request(plan, actor="new operator")
    assert reopened.reviewed is None
    assert (
        resolve_launch_config(lab, reopened).reviewed.binding
        == definition.scientific_binding
    )


def test_explicit_clear_in_prepare_does_not_inherit_session_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scopecat.application.author_project as author_module
    from scopecat.application.author_project import AuthorProject
    from scopecat.application.session_context import INHERIT
    from scopecat.records.scientific_selection import UnboundSubjectChoice

    def accept(_lab: LabClient, _request: LaunchRequest) -> None:
        pass

    monkeypatch.setattr(author_module, "resolve_launch_config", accept)
    with AuthorProject("http://test") as session:
        session.use(sample="chip")
        science = session._prepare_science(
            selection=INHERIT,
            target=INHERIT,
            context=None,
            sample=INHERIT,
            batch=INHERIT,
            parameters=None,
            candidate=None,
            overrides=(),
        )
        assert isinstance(science.subject, UnboundSubjectChoice)
        assert isinstance(session.selection.science.subject, SampleSubjectChoice)
