"""Ordinary Python context selection through real HTTP admission and retained runs."""

from __future__ import annotations

from uuid import uuid4

import numpy as np
import pytest
import scopecat as sc
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.daemon.wire import RunAdmission, RunSubmission
from scopecat.records.parameter_revision import ParameterRevision, ParameterRevisionRef
from scopecat.records.run import ParameterRunConfigSource
from scopecat.records.sample import SampleRevisionDraft

from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.exploration import exploration_cases
from reference_lab.parameters import QubitParameters
from reference_lab.workflows.exploratory_signal import exploratory_signal


def test_contexts_select_parameters_and_preserve_sample_and_run_history(
    monkeypatch: pytest.MonkeyPatch,
    independent_lab_daemon: str,
) -> None:
    with create_application(EXAMPLE_ROOT).connect(independent_lab_daemon) as lab:
        setup = lab.setup.active()
        key = uuid4().hex
        for sample in ("a", "b"):
            lab.samples.create(
                f"context-{key}-{sample}",
                kind="synthetic",
                content=SampleRevisionDraft(display_name=f"Context {sample}"),
            )
        original_id = None
        original_snapshot = None
        original_values = None
        refs: list[ParameterRevisionRef] = []
        for index, case in enumerate(exploration_cases()):
            sample_id = case.sample_id.replace("exploration-", f"context-{key}-")
            saved = lab.parameters.save(
                name=f"{sample_id}-{case.context_id}",
                catalog=case.config.parameter_catalog,
                parameters=case.config.parameter_snapshot,
            )
            ref = saved.ref
            refs.append(ref)
            resolved = lab.parameters.resolve(ref, setup=setup.revision.ref)
            prepared = lab.prepare(
                exploratory_signal.build(),
                config=resolved,
            )
            selector = lab.samples.handle(sample_id).selector(revision=1)
            assert prepared.preview(sample=selector).point_count == 5
            run = prepared.run(sample=selector)
            assert run.status == "completed"
            assert isinstance(run.snapshot.config_source, ParameterRunConfigSource)
            assert run.snapshot.config_source.parameters == ref
            assert run.snapshot.config_source.setup == setup.revision.ref
            assert run.samples[0].sample_id == sample_id
            assert run.samples[0].revision == 1
            assert (
                np.argmax(np.asarray(run.measurements()["result"].require_values()))
                == index + 1
            )
            if index == 0:
                original_id, original_snapshot = run.id, run.snapshot
                original_values = np.asarray(
                    run.measurements()["result"].require_values()
                ).copy()
        trial = lab.parameters.resolve(
            refs[0],
            setup=setup.revision.ref,
            overrides=(
                sc.parameter_update(
                    QubitParameters.drive_carrier_frequency,
                    sc.EntityRef(id="q0", kind="logical_qubit"),
                    sc.Quantity(5.1, "GHz"),
                ),
            ),
        )
        submit_run = DaemonClient.submit_run

        def tamper_source(
            client: DaemonClient, submission: RunSubmission
        ) -> RunAdmission:
            source = submission.config_source
            assert isinstance(source, ParameterRunConfigSource)
            forged = source.model_copy(update={"parameters": refs[1]})
            return submit_run(
                client, submission.model_copy(update={"config_source": forged})
            )

        with monkeypatch.context() as patch:
            patch.setattr(DaemonClient, "submit_run", tamper_source)
            with pytest.raises(DaemonConflictError, match="exact resolved inputs"):
                lab.run(
                    exploratory_signal.build(),
                    config=lab.parameters.resolve(refs[0], setup=setup.revision.ref),
                    sample=lab.samples.handle(f"context-{key}-a"),
                )

        assert trial.config_source.overrides
        trial_run = lab.run(
            exploratory_signal.build(),
            config=trial,
            sample=lab.samples.handle(f"context-{key}-a"),
        )
        assert (
            np.argmax(np.asarray(trial_run.measurements()["result"].require_values()))
            == 4
        )
        restored = lab.run(
            exploratory_signal.build(),
            config=lab.parameters.resolve(refs[0], setup=setup.revision.ref),
            sample=lab.samples.handle(f"context-{key}-a"),
        )
        assert (
            np.argmax(np.asarray(restored.measurements()["result"].require_values()))
            == 1
        )
        assert lab.setup.active() == setup
        assert lab.config.registry().entries == ()
        assert original_id is not None
        assert original_snapshot is not None
        assert original_values is not None
        original = lab.get_run(original_id)
        assert original.snapshot == original_snapshot
        np.testing.assert_array_equal(
            original.measurements()["result"].require_values(), original_values
        )


def test_context_launch_survives_unrelated_parameter_publication(
    independent_lab_daemon: str, independent_parameters: ParameterRevision
) -> None:
    from scopecat.application.launch import LaunchPreview, LaunchSubmission
    from scopecat.records.launch_request import LaunchRequest
    from scopecat.records.scientific_selection import (
        ParameterConfiguration,
        SampleSubjectChoice,
        ScientificSelection,
    )

    application = create_application(EXAMPLE_ROOT)
    assert application.launch_provider is not None
    with application.connect(independent_lab_daemon) as lab:
        setup = lab.setup.active()
        key = uuid4().hex
        sample_id = f"context-replay-{key}"
        lab.samples.create(
            sample_id,
            kind="synthetic",
            content=SampleRevisionDraft(display_name="Replay"),
        )
        branch = lab.parameters.create_branch(
            f"replay-{key}", revision=independent_parameters
        )
        ref = independent_parameters.ref
        resolved = lab.parameters.resolve(ref, setup=setup.revision.ref)
        lab.samples.revise(sample_id, SampleRevisionDraft(display_name="Replay r2"))
        assert application.authors is not None
        request = LaunchRequest(
            action="preview",
            experiment="reference_lab.frequency_amplitude",
            version=application.authors.get(
                "reference_lab.frequency_amplitude"
            ).entry.version,
            selection=ScientificSelection(
                subject=SampleSubjectChoice(sample_id=sample_id, revision=1),
                configuration=ParameterConfiguration(ref=ref, setup=setup.revision.ref),
            ),
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
                "reviewed": preview.reviewed,
                "manual_state": preview.manual_state,
                "expected_request_hash": preview.request_hash,
            }
        )
        admitted = application.launch_provider(lab, command)
        assert isinstance(admitted, LaunchSubmission)
        procedure = lab.procedures.get(admitted.procedure_id)
        assert procedure.snapshot.samples[0].revision == 1
        procedure.resume()
        output = procedure.output("experiment")
        assert output.kind == "run"
        assert lab.get_run(output.run_id).samples[0].revision == 1
        assert application.authors is not None
        selected_author = application.authors.get("signal")
        author_run = selected_author.run(
            lab,
            config=resolved,
            sample=lab.samples.handle(sample_id).selector(revision=1),
        )
        assert author_run.samples[0].revision == 1
        editor = lab.parameters.workspace(branch.name)
        editor["qubits"]["q0"]["drive_carrier_frequency"] = sc.Quantity(5.1, "GHz")
        editor.save()
        assert application.launch_provider(lab, command) == admitted
        submitted = application.launch_provider(
            lab, command.model_copy(update={"request_key": "new-context-replay"})
        )
        assert isinstance(submitted, LaunchSubmission)
        next_procedure = lab.procedures.get(submitted.procedure_id)
        next_procedure.resume()
        output = next_procedure.output("experiment")
        assert output.kind == "run"
        retained = lab.get_run(output.run_id)
        assert retained.snapshot.scientific_binding == preview.reviewed.binding
        assert retained.config == resolved.config
        assert lab.setup.active() == setup
        assert lab.config.registry().entries == ()
