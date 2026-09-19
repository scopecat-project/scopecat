"""One physical chip keeps its identity while cooldown applicability changes."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
import scopecat as sc
from scopecat.api.lab import LabClient
from scopecat.application.author_project import AuthorProject
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.experimental_batch import ExperimentalBatchEdit
from scopecat.records.research_project import RunHistoryFilter
from scopecat.records.sample import SampleRevisionDraft

from reference_lab.parameters import QubitParameters
from reference_lab.workflows.authored.signal import signal

pytestmark = pytest.mark.usefixtures("reference_lab_daemon")


def test_new_batch_requires_its_own_working_point(tmp_path: Path) -> None:
    endpoint = os.environ["SCOPECAT_DAEMON_URL"]
    key = uuid4().hex
    with (
        AuthorProject(endpoint, receipts=tmp_path / "receipts") as session,
        LabClient(DaemonClient(endpoint)) as lab,
    ):
        active = lab.config.active()
        base = ConfigContextRef(
            entry_id=active.entry.id, content_hash=active.entry.content_hash
        )
        chip = lab.samples.create(
            f"batch-chip-{key}",
            kind="synthetic",
            content=SampleRevisionDraft(display_name="One physical chip"),
        )
        first = session.create_experimental_batch("Cooldown 1")
        second = session.create_experimental_batch("Cooldown 2")
        collection = session.create_record_collection("Continuous chip study")
        saved = lab.config.save_context(
            entry_id=f"batch-wp-{key}-a",
            base=base,
            sample=chip.selector(batch_id=first.id),
            working_point_id="parked",
            label="First cooldown working point",
        )
        ref = ConfigContextRef(
            entry_id=saved.entry.id, content_hash=saved.entry.content_hash
        )
        session.use(working_point=ref, collection=collection.id)
        assert session.selection.science.batch.kind == "declared"
        assert session.selection.science.batch.id == first.id
        prepared = session.prepare("signal")
        plan = prepared.save_plan("Original cooldown recipe", saved_by="operator")
        original = prepared.run().wait(timeout=60).result()
        selection = session.selection
        with pytest.raises(ValueError, match="selected subject/batch differs"):
            session.use(batch=second.id)
        assert session.selection == selection
        with pytest.raises(ValueError, match="selected subject/batch differs"):
            session.prepare("signal", context=ref, batch=second.id)
        # Explicit copying creates a new scoped estimate; advancing the old
        # workspace in place must never relabel its physical event.
        with pytest.raises(DaemonConflictError, match="cannot change"):
            lab.config.save_context(
                entry_id=f"batch-wp-{key}-bad",
                base=ref,
                sample=chip.selector(batch_id=second.id),
                working_point_id="parked",
                label="Cannot move old workspace",
                advance=True,
            )
        copied = lab.config.save_context(
            entry_id=f"batch-wp-{key}-b",
            base=ref,
            sample=chip.selector(batch_id=second.id),
            working_point_id="parked",
            label="Copied estimates for second cooldown",
        )
        copied_ref = ConfigContextRef(
            entry_id=copied.entry.id, content_hash=copied.entry.content_hash
        )
        assert copied.config.parameter_snapshot == saved.config.parameter_snapshot
        session.use(working_point=copied_ref)
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
            session.prepare("signal", context=ref)
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
                sample=chip.selector(batch_id=second.id, context_id="parked"),
            )
        verified = lab.run(
            signal,
            config=candidate,
            sample=chip.selector(batch_id=first.id, context_id="parked"),
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
        assert lab.config.active() == active
    with AuthorProject(endpoint) as reopened:
        assert reopened.experimental_batch(first.id).name == "Renamed cooldown"
        assert reopened.run(original.id).samples[0].batch_id == first.id
