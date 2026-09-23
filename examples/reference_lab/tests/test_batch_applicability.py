"""One physical chip keeps its identity while cooldown applicability changes."""

from pathlib import Path
from uuid import uuid4

import pytest
import scopecat as sc
from scopecat.api.lab import LabClient
from scopecat.application.author_project import AuthorProject
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.records.experimental_batch import ExperimentalBatchEdit
from scopecat.records.parameter_revision import ParameterRevision
from scopecat.records.research_project import RunHistoryFilter
from scopecat.records.run import ParameterRunConfigSource
from scopecat.records.sample import SampleRevisionDraft

from reference_lab.parameters import QubitParameters
from reference_lab.workflows.authored.signal import signal


def test_batch_selection_preserves_frozen_evidence(
    tmp_path: Path,
    independent_lab_daemon: str,
    independent_parameters: ParameterRevision,
) -> None:
    endpoint = independent_lab_daemon
    key = uuid4().hex
    with (
        AuthorProject(endpoint, receipts=tmp_path / "receipts") as session,
        LabClient(DaemonClient(endpoint)) as lab,
    ):
        setup = lab.setup.active()
        chip = lab.samples.create(
            f"batch-chip-{key}",
            kind="synthetic",
            content=SampleRevisionDraft(display_name="One physical chip"),
        )
        first = session.create_experimental_batch("Cooldown 1")
        second = session.create_experimental_batch("Cooldown 2")
        collection = session.create_record_collection("Continuous chip study")
        session.use(
            sample=chip.id,
            batch=first.id,
            parameters=independent_parameters.ref,
            setup=setup.revision.ref,
            collection=collection.id,
        )
        assert session.selection.science.batch.kind == "declared"
        assert session.selection.science.batch.id == first.id
        prepared = session.prepare("signal")
        plan = prepared.save_plan("Original cooldown plan", saved_by="operator")
        original = prepared.run().wait(timeout=60).result()
        original_selection = session.selection.science
        # Reusing numbers in another cooldown is allowed; it asserts no validity.
        # Changing this local choice cannot relabel frozen work or old evidence.
        session.use(batch=second.id)
        assert (
            session.selection.science.configuration == original_selection.configuration
        )
        assert prepared.request.selection == original_selection
        assert session.selection.science.batch.kind == "declared"
        assert session.selection.science.batch.id == second.id
        assert session.selection.science.subject.kind == "sample"
        assert session.selection.science.subject.sample_id == chip.id
        reopened = session.prepare_plan(plan.ref)
        assert reopened.request.selection.batch.kind == "declared"
        assert reopened.request.selection.batch.id == first.id
        with pytest.raises(ValueError, match="batch does not match"):
            session.prepare_plan(plan.ref, batch=second.id)
        assert (
            session.prepare("signal", selection=original_selection)
            .preview.reviewed.binding.samples[0]
            .batch_id
            == first.id
        )
        candidate = (
            original.analysis("Batch-scoped estimate")
            .result()
            .propose(
                "carrier",
                sc.parameter_update(
                    QubitParameters.drive_carrier_frequency,
                    sc.EntityRef(id="q0", kind="logical_qubit"),
                    sc.Quantity(4.85, "GHz"),
                ),
            )
            .save()
            .candidate_config()
        )
        assert (
            session.prepare("signal", candidate=candidate)
            .preview.reviewed.binding.samples[0]
            .batch_id
            == first.id
        )
        with pytest.raises(
            DaemonConflictError, match="original scientific subject and batch"
        ):
            lab.run(
                signal,
                config=candidate,
                sample=chip.selector(batch_id=second.id),
            )
        verified = lab.run(
            signal,
            config=candidate,
            sample=chip.selector(batch_id=first.id),
        )
        assert verified.samples[0].batch_id == first.id
        later = session.prepare("signal").run().wait(timeout=60).result()
        assert original.samples[0].sample_id == later.samples[0].sample_id == chip.id
        assert original.samples[0].revision == later.samples[0].revision == 1
        assert original.samples[0].batch_id == first.id
        assert later.samples[0].batch_id == second.id
        assert session.run_number(original) == 1 and session.run_number(later) == 2
        assert {
            run.run_id
            for run in session.list_runs(
                history=RunHistoryFilter(batch_id=first.id)
            ).items
        } == {original.id, verified.id}
        old_snapshot = original.snapshot
        session.save_experimental_batch(
            first.id,
            ExperimentalBatchEdit(name="Renamed cooldown", expected_revision=1),
        )
        assert original.snapshot == old_snapshot
        address = session.get_run(original.id).address
        assert address is not None and address.number == 1
        for run in (original, later):
            assert isinstance(run.snapshot.config_source, ParameterRunConfigSource)
            assert run.snapshot.config_source.parameters == independent_parameters.ref
            assert run.snapshot.config_source.setup == setup.revision.ref
        assert lab.setup.active() == setup
        assert lab.config.registry().entries == ()
    with AuthorProject(endpoint) as reopened:
        assert reopened.experimental_batch(first.id).name == "Renamed cooldown"
        assert reopened.run(original.id).samples[0].batch_id == first.id
