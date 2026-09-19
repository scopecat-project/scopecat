"""Independent author clients select contexts while prepared work stays frozen."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from scopecat.api.lab import LabClient
from scopecat.application.author_project import AuthorProject
from scopecat.daemon.client import DaemonClient, DaemonNotFoundError
from scopecat.records.config_context import ConfigContextRef, ContextRunConfigSource
from scopecat.records.sample import SampleRevisionDraft

pytestmark = pytest.mark.usefixtures("reference_lab_daemon")


def test_session_selection_is_local_and_preparation_is_frozen(tmp_path: Path) -> None:
    endpoint = os.environ["SCOPECAT_DAEMON_URL"]
    key = uuid4().hex
    with (
        AuthorProject(endpoint, receipts=tmp_path / "first") as first,
        AuthorProject(endpoint, receipts=tmp_path / "second") as second,
        LabClient(DaemonClient(endpoint)) as lab,
    ):
        active = lab.config.active()
        base = ConfigContextRef(
            entry_id=active.entry.id, content_hash=active.entry.content_hash
        )
        refs: list[ConfigContextRef] = []
        samples: list[str] = []
        for suffix in ("a", "b"):
            sample = lab.samples.create(
                f"session-{key}-{suffix}",
                kind="synthetic",
                content=SampleRevisionDraft(display_name=f"Session {suffix}"),
            )
            saved = lab.config.save_context(
                entry_id=f"session-{key}-{suffix}",
                base=base,
                sample=sample.selector(),
                working_point_id="working-point",
                label=f"Working point {suffix}",
            )
            samples.append(sample.id)
            refs.append(
                ConfigContextRef(
                    entry_id=saved.entry.id, content_hash=saved.entry.content_hash
                )
            )
        alpha = first.create_record_collection("First collection")
        beta = second.create_record_collection("Second collection")
        selected = first.use(
            working_point=refs[0], collection=alpha.id, operator="alice"
        )
        assert selected.sample == samples[0]
        assert second.selection.collection is None
        second.use(working_point=refs[1], collection=beta.id, operator="bob")
        prepared = first.prepare("signal")
        assert prepared.request.context == refs[0]
        assert prepared.request.actor == "alice"
        frozen = prepared.request.model_dump_json()
        # A partial update preserves independent choices and never mutates old work.
        first.use(working_point=refs[1], operator="carol")
        assert first.selection.collection == alpha.id
        assert first.selection.sample == samples[1]
        assert prepared.request.model_dump_json() == frozen
        assert second.selection.operator == "bob"
        before_failure = first.selection
        with pytest.raises(ValueError, match="sample does not match"):
            first.use(sample=samples[0])
        assert first.selection == before_failure
        with pytest.raises(DaemonNotFoundError):
            first.use(collection="missing-session-collection", operator="not-accepted")
        assert first.selection == before_failure
        # An explicit scientific override must not inherit another sample/workpoint.
        override = first.prepare("signal", context=refs[0])
        assert override.request.context == refs[0]
        assert override.request.sample is None
        assert override.preview.sample_binding is not None
        assert override.preview.sample_binding.sample_id == samples[0]
        assert override.request.record_collection == alpha.id
        from_workspace = first.prepare(
            "signal", parameters=first.config.workspace(context=refs[0].entry_id)
        )
        assert from_workspace.request.context == refs[0]
        assert from_workspace.request.sample == samples[0]
        assert from_workspace.request.record_collection == alpha.id
        clear = first.prepare("signal", context=None, record_collection=None)
        assert clear.request.context is None and clear.request.sample is None
        assert clear.request.record_collection is None
        assert first.selection == before_failure

        old = prepared.run().wait(timeout=60).result()
        newer = first.prepare("signal").run().wait(timeout=60).result()
        other = second.prepare("signal").run().wait(timeout=60).result()
        assert first.run(1).id == old.id
        assert first.run(2).id == newer.id
        assert second.run(1).id == other.id
        assert first.run_number(old) == second.run_number(other) == 1
        assert first.run_number(newer) == 2
        assert {item.run_id for item in first.history().page.items} == {
            old.id,
            newer.id,
        }
        assert {item.run_id for item in second.history().page.items} == {other.id}
        assert first.run(other.id).id == other.id  # durable IDs ignore local defaults
        with pytest.raises(ValueError, match="another record collection"):
            first.run_number(other.id)
        for run, sample_id, ref, actor in (
            (old, samples[0], refs[0], "alice"),
            (newer, samples[1], refs[1], "carol"),
            (other, samples[1], refs[1], "bob"),
        ):
            assert run.samples[0].sample_id == sample_id
            assert isinstance(run.snapshot.config_source, ContextRunConfigSource)
            assert run.snapshot.config_source.context == ref
            assert run.request.operator == actor
        # Saved recipes retain scientific scope and inherit only destination/actor.
        plan = prepared.save_plan("Frozen A recipe", saved_by="alice")
        reopened = second.prepare_plan(plan.ref)
        assert reopened.request.context == refs[0]
        assert reopened.request.actor == "bob"
        assert reopened.request.record_collection == beta.id
        assert lab.config.active() == active
        second.use(sample=None, working_point=None, collection=None)
        assert second.selection.operator == "bob"
        global_number = second.run_number(other.id)
        assert second.run(global_number).id == other.id
        assert first.selection == before_failure
    with AuthorProject(endpoint) as reopened:
        assert reopened.selection.collection is None
        assert reopened.selection.working_point is None
        assert reopened.selection.operator == "operator"
