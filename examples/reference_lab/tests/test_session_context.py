"""Independent author clients select contexts while prepared work stays frozen."""

from pathlib import Path
from uuid import uuid4

import pytest
import scopecat as sc
from scopecat.api.lab import LabClient
from scopecat.application.author_project import AuthorProject
from scopecat.daemon.client import DaemonClient, DaemonNotFoundError
from scopecat.records.parameter_revision import ParameterRevision
from scopecat.records.run import ParameterRunConfigSource
from scopecat.records.sample import SampleRevisionDraft
from scopecat.records.scientific_selection import (
    ParameterConfiguration,
    ScientificSelection,
)


def test_session_selection_is_local_and_preparation_is_frozen(
    tmp_path: Path,
    independent_lab_daemon: str,
    independent_parameters: ParameterRevision,
) -> None:
    endpoint = independent_lab_daemon
    key = uuid4().hex
    with (
        AuthorProject(endpoint, receipts=tmp_path / "first") as first,
        AuthorProject(endpoint, receipts=tmp_path / "second") as second,
        LabClient(DaemonClient(endpoint)) as lab,
    ):
        setup = lab.setup.active()
        branch = lab.parameters.create_branch(
            f"session-{key}", revision=independent_parameters
        )
        editor = lab.parameters.workspace(branch.name)
        editor["qubits"]["q0"]["drive_carrier_frequency"] = sc.Quantity(4.85, "GHz")
        changed = editor.save(f"session-changed-{key}")
        refs = [independent_parameters.ref, changed.ref]
        samples: list[str] = []
        for suffix in ("a", "b"):
            sample = lab.samples.create(
                f"session-{key}-{suffix}",
                kind="synthetic",
                content=SampleRevisionDraft(display_name=f"Session {suffix}"),
            )
            samples.append(sample.id)
        alpha = first.create_record_collection("First collection")
        beta = second.create_record_collection("Second collection")
        selected = first.use(
            sample=samples[0],
            parameters=refs[0],
            setup=setup.revision.ref,
            collection=alpha.id,
            operator="alice",
        )
        assert selected.science.subject.kind == "sample"
        assert selected.science.subject.sample_id == samples[0]
        assert second.selection.collection is None
        second.use(
            sample=samples[1],
            parameters=refs[1],
            setup=setup.revision.ref,
            collection=beta.id,
            operator="bob",
        )
        prepared = first.prepare("signal")
        assert prepared.request.selection.configuration.kind == "parameters"
        assert prepared.request.selection.configuration.ref == refs[0]
        assert prepared.request.actor == "alice"
        frozen = prepared.request.model_dump_json()
        # A partial update preserves independent choices and never mutates old work.
        first.use(
            sample=samples[1],
            parameters=refs[1],
            setup=setup.revision.ref,
            operator="carol",
        )
        assert first.selection.collection == alpha.id
        assert first.selection.science.subject.kind == "sample"
        assert first.selection.science.subject.sample_id == samples[1]
        assert prepared.request.model_dump_json() == frozen
        assert second.selection.operator == "bob"
        before_failure = first.selection
        with pytest.raises(DaemonNotFoundError):
            first.use(parameters="missing-session-parameters", operator="not-accepted")
        assert first.selection == before_failure
        with pytest.raises(DaemonNotFoundError):
            first.use(collection="missing-session-collection", operator="not-accepted")
        assert first.selection == before_failure
        # An explicit scientific selection replaces scope but inherits destination.
        override = first.prepare("signal", selection=selected.science)
        assert override.request.selection.configuration.kind == "parameters"
        assert override.request.selection.configuration.ref == refs[0]
        assert override.request.selection.subject.kind == "sample"
        assert override.preview.reviewed.binding.samples
        assert override.preview.reviewed.binding.samples[0].sample_id == samples[0]
        assert override.request.record_collection == alpha.id
        from_workspace = first.prepare(
            "signal", parameters=first.parameters.workspace(branch.name)
        )
        assert from_workspace.request.selection.configuration.kind == "parameters"
        assert from_workspace.request.selection.configuration.ref == refs[0]
        assert from_workspace.request.selection.subject.kind == "sample"
        assert from_workspace.request.selection.subject.sample_id == samples[1]
        assert from_workspace.request.record_collection == alpha.id
        clear = first.prepare(
            "signal",
            selection=ScientificSelection(
                configuration=ParameterConfiguration(
                    ref=refs[0], setup=setup.revision.ref
                )
            ),
            record_collection=None,
        )
        assert clear.request.selection.configuration.kind == "parameters"
        assert clear.request.selection.subject.kind == "unbound"
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
            assert isinstance(run.snapshot.config_source, ParameterRunConfigSource)
            assert run.snapshot.config_source.parameters == ref
            assert run.snapshot.config_source.setup == setup.revision.ref
            assert run.request.operator == actor
        # Saved plans retain scientific scope and inherit only destination/actor.
        plan = prepared.save_plan("Frozen A plan", saved_by="alice")
        reopened = second.prepare_plan(plan.ref)
        assert reopened.request.selection.configuration.kind == "parameters"
        assert reopened.request.selection.configuration.ref == refs[0]
        assert reopened.request.actor == "bob"
        assert reopened.request.record_collection == beta.id
        assert lab.setup.active() == setup
        assert lab.config.registry().entries == ()
        second.use(collection=None)
        assert second.selection.operator == "bob"
        global_number = second.run_number(other.id)
        assert second.run(global_number).id == other.id
        assert first.selection == before_failure
    with AuthorProject(endpoint) as reopened:
        assert reopened.selection.collection is None
        assert reopened.selection.science.configuration.kind == "active"
        assert reopened.selection.operator == "operator"
