"""Ordinary Python context selection through real HTTP admission and retained runs."""

from __future__ import annotations

import os

import numpy as np
import pytest
import scopecat as sc
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.daemon.wire import RunAdmission, RunSubmission
from scopecat.records.config_context import ConfigContextRef, ContextRunConfigSource
from scopecat.records.parameter import ParameterSnapshot, TableParameterValue
from scopecat.records.sample import SampleRevisionDraft

from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.exploration import exploration_cases, exploration_config
from reference_lab.parameters import (
    DRAG_BETA,
    DRIVE_CARRIER_FREQUENCY,
    Q0,
    QUBIT,
    QUBITS,
)
from reference_lab.workflows.exploratory_signal import exploratory_signal


def test_contexts_select_parameters_and_preserve_sample_and_run_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with create_application(EXAMPLE_ROOT).connect(
        os.environ["SCOPECAT_DAEMON_URL"]
    ) as lab:
        active = lab.config.active()
        base = ConfigContextRef(
            entry_id=active.entry.id, content_hash=active.entry.content_hash
        )
        for sample in ("a", "b"):
            lab.samples.create(
                f"context-{sample}",
                kind="synthetic",
                content=SampleRevisionDraft(display_name=f"Context {sample}"),
            )
        original_id = None
        original_snapshot = None
        original_values = None
        refs: list[ConfigContextRef] = []
        for index, case in enumerate(exploration_cases()):
            sample_id = case.sample_id.replace("exploration-", "context-")
            saved = lab.config.save_context(
                entry_id=f"{sample_id}-{case.context_id}",
                base=base,
                sample=lab.samples.handle(sample_id).selector(),
                working_point_id=case.context_id,
                label=f"{sample_id} {case.context_id}",
                parameters=case.config.parameter_snapshot,
            )
            ref = ConfigContextRef(
                entry_id=saved.entry.id, content_hash=saved.entry.content_hash
            )
            refs.append(ref)
            resolved = lab.config.resolve_context(ref)
            prepared = lab.prepare(exploratory_signal(), config=resolved)
            assert prepared.preview().point_count == 5
            run = prepared.run()
            assert run.status == "completed"
            assert isinstance(run.snapshot.config_source, ContextRunConfigSource)
            assert run.snapshot.config_source.context == ref
            assert run.samples[0].sample_id == sample_id
            assert run.samples[0].revision == 1
            assert run.samples[0].context_id == case.context_id
            assert (
                np.argmax(np.asarray(run.measurements()["result"].require_values()))
                == index + 1
            )
            if index == 0:
                original_id, original_snapshot = run.id, run.snapshot
                original_values = np.asarray(
                    run.measurements()["result"].require_values()
                ).copy()
        trial = lab.config.resolve_context(
            refs[0],
            overrides=(Q0[DRIVE_CARRIER_FREQUENCY].update(sc.Quantity(5.1, "GHz")),),
        )
        submit_run = DaemonClient.submit_run

        def tamper_source(
            client: DaemonClient, submission: RunSubmission
        ) -> RunAdmission:
            source = submission.config_source
            assert isinstance(source, ContextRunConfigSource)
            forged = source.model_copy(
                update={
                    "sample": source.sample.model_copy(
                        update={"sample_id": "context-b"}
                    )
                }
            )
            return submit_run(
                client, submission.model_copy(update={"config_source": forged})
            )

        with monkeypatch.context() as patch:
            patch.setattr(DaemonClient, "submit_run", tamper_source)
            with pytest.raises(
                DaemonConflictError, match="sample identity was changed"
            ):
                lab.run(exploratory_signal(), config=refs[0])

        def stale_source(
            client: DaemonClient, submission: RunSubmission
        ) -> RunAdmission:
            source = submission.config_source
            assert isinstance(source, ContextRunConfigSource)
            stale = source.model_copy(
                update={"lab_generation": source.lab_generation + 1}
            )
            return submit_run(
                client, submission.model_copy(update={"config_source": stale})
            )

        with monkeypatch.context() as patch:
            patch.setattr(DaemonClient, "submit_run", stale_source)
            with pytest.raises(
                DaemonConflictError, match="changed since context resolution"
            ):
                lab.run(exploratory_signal(), config=refs[0])

        assert trial.config_source.overrides
        trial_run = lab.run(exploratory_signal(), config=trial)
        assert (
            np.argmax(np.asarray(trial_run.measurements()["result"].require_values()))
            == 4
        )
        restored = lab.run(exploratory_signal(), config=refs[0])
        assert (
            np.argmax(np.asarray(restored.measurements()["result"].require_values()))
            == 1
        )
        with pytest.raises(ValueError, match="sample does not match"):
            lab.run(
                exploratory_signal(),
                config=refs[0],
                sample=lab.samples.handle("context-b"),
            )
        assert lab.config.active() == active
        assert original_id is not None
        assert original_snapshot is not None
        assert original_values is not None
        original = lab.get_run(original_id)
        assert original.snapshot == original_snapshot
        np.testing.assert_array_equal(
            original.measurements()["result"].require_values(), original_values
        )


def test_context_unknown_values_block_only_the_experiment_that_needs_them() -> None:
    with create_application(EXAMPLE_ROOT).connect(
        os.environ["SCOPECAT_DAEMON_URL"]
    ) as lab:
        active = lab.config.active()
        lab.samples.create(
            "context-unknown",
            kind="synthetic",
            content=SampleRevisionDraft(display_name="Unknown values"),
        )
        config = exploration_config(None)
        table = config.parameter_snapshot.get(QUBITS.id)
        assert isinstance(table, TableParameterValue)
        rows = [dict(row) for row in table.rows]
        for row in rows:
            if row[QUBIT.id] == Q0.key[0].value:
                row.pop(DRAG_BETA.id)
        parameters = ParameterSnapshot(
            id=config.parameter_snapshot.id,
            values=tuple(
                TableParameterValue(id=QUBITS.id, rows=rows)
                if value.id == QUBITS.id
                else value
                for value in config.parameter_snapshot.values
            ),
        )
        saved = lab.config.save_context(
            entry_id="unknown-context",
            base=ConfigContextRef(
                entry_id=active.entry.id, content_hash=active.entry.content_hash
            ),
            sample=lab.samples.handle("context-unknown").selector(),
            working_point_id="unknown",
            label="Unknown",
            parameters=parameters,
        )
        ref = ConfigContextRef(
            entry_id=saved.entry.id, content_hash=saved.entry.content_hash
        )
        with pytest.raises((ValueError, KeyError), match="drive_carrier_frequency"):
            lab.preview(exploratory_signal(), config=ref)
        resolved = lab.config.resolve_context(
            ref,
            overrides=(Q0[DRIVE_CARRIER_FREQUENCY].update(sc.Quantity(4.8, "GHz")),),
        )
        assert any(DRAG_BETA.id in item for item in resolved.missing_values)
        assert not any(
            DRIVE_CARRIER_FREQUENCY.id in item for item in resolved.missing_values
        )
        assert lab.run(exploratory_signal(), config=resolved).status == "completed"
        assert lab.config.active() == active


def test_context_launch_replay_preserves_reviewed_generation() -> None:
    from scopecat.application.launch import LaunchPreview, LaunchSubmission
    from scopecat.records.launch_request import LaunchRequest

    application = create_application(EXAMPLE_ROOT)
    assert application.launch_provider is not None
    with application.connect(os.environ["SCOPECAT_DAEMON_URL"]) as lab:
        active = lab.config.active()
        lab.samples.create(
            "context-replay",
            kind="synthetic",
            content=SampleRevisionDraft(display_name="Replay"),
        )
        saved = lab.config.save_context(
            entry_id="context-replay-1",
            base=ConfigContextRef(
                entry_id=active.entry.id, content_hash=active.entry.content_hash
            ),
            sample=lab.samples.handle("context-replay").selector(),
            working_point_id="parked",
            label="Replay",
        )
        ref = ConfigContextRef(
            entry_id=saved.entry.id, content_hash=saved.entry.content_hash
        )
        lab.samples.revise(
            "context-replay", SampleRevisionDraft(display_name="Replay r2")
        )
        request = LaunchRequest(
            action="preview", experiment="frequency-amplitude", version="1", context=ref
        )
        preview = application.launch_provider(lab, request)
        assert isinstance(preview, LaunchPreview)
        assert preview.preflight is not None
        assert preview.preflight.stages[0].configuration == "selected_context"
        assert (
            "accepted" not in preview.preflight.stages[0].configuration_meaning.lower()
        )
        command = LaunchRequest.model_validate(
            {
                **request.model_dump(),
                "action": "submit",
                "request_key": "context-replay",
                "config_source": preview.config_source,
                "manual_state": preview.manual_state,
                "expected_request_hash": preview.request_hash,
            }
        )
        admitted = application.launch_provider(lab, command)
        assert isinstance(admitted, LaunchSubmission)
        procedure = lab.procedures.get(admitted.procedure_id)
        assert procedure.snapshot.samples[0].revision == 1
        procedure.resume()
        output = procedure.output("signal")
        assert output.kind == "run"
        assert lab.get_run(output.run_id).samples[0].revision == 1
        assert application.authors is not None
        selected_author = application.authors.get("signal")
        author_run = selected_author.run(lab, config=ref)
        assert author_run.samples[0].revision == 1
        lab.config.set_default(active.config)
        assert application.launch_provider(lab, command) == admitted
        with pytest.raises(DaemonConflictError, match="active configuration changed"):
            application.launch_provider(
                lab, command.model_copy(update={"request_key": "new-context-replay"})
            )
