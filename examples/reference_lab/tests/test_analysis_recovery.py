"""A real daemon retains acquisition across an explicit new analysis procedure."""

from pathlib import Path

import pytest
from scopecat.automation import (
    AnalysisPublicationOutputRef,
    ProcedureSubmitCommand,
    RunOutputRef,
)
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.records.parameter_revision import ParameterRevision

from reference_lab.configuration import EXAMPLE_ROOT


def test_failed_analysis_recovers_without_reacquisition(
    independent_lab_daemon: str,
    independent_parameters: ParameterRevision,
) -> None:
    from reference_lab.application import create_application
    from reference_lab.workflows.analysis_recovery import (
        TEMPERATURE_ANALYSIS_RECOVERY,
        failed_temperature_analysis,
    )
    from reference_lab.workflows.temperature_diagnostic import (
        TemperatureDiagnosticIntent,
    )

    with create_application(Path(EXAMPLE_ROOT)).connect(independent_lab_daemon) as lab:
        setup = lab.setup.active()
        inputs = lab.parameters.resolve(
            independent_parameters, setup=setup.revision.ref
        )
        before_runs = {run.id for run in lab.runs().items}
        source = lab.procedures.submit(
            failed_temperature_analysis,
            TemperatureDiagnosticIntent(initial_config=inputs.config),
            request_key="known-software-failure",
        )
        with pytest.raises(ValueError, match="demonstration software analysis failure"):
            source.resume()
        original = source.snapshot
        original_history = source.steps().model_dump_json()
        assert original.closure is not None
        assert original.closure.status == "failed"
        acquired = source.step("sample").output
        assert isinstance(acquired, RunOutputRef)
        retained = lab.get_run(acquired.run_id)
        original_measurements = retained.measurements()["temperature"].require_values()
        run_ids = {run.id for run in lab.runs().items}
        assert run_ids - before_runs == {acquired.run_id}

        available = lab.procedures.recovery_availability(
            TEMPERATURE_ANALYSIS_RECOVERY, source.id
        )
        assert available.reason is None
        plan = available.plan
        assert plan is not None
        assert plan.recovery.retained_run == acquired
        assert plan.recovery.definition == original.definition
        with DaemonClient(independent_lab_daemon) as raw:
            forged = plan.recovery.model_copy(
                update={"revision": original.revision + 1}
            )
            with pytest.raises(DaemonConflictError, match="identity or revision"):
                raw.submit_procedure(
                    ProcedureSubmitCommand(
                        request_key="forged",
                        definition=plan.definition,
                        intent=plan.intent,
                        samples=plan.samples,
                        recovery=forged,
                    )
                )
        changed = plan.model_copy(
            update={
                "recovery": plan.recovery.model_copy(
                    update={
                        "definition": original.definition.model_copy(
                            update={"fingerprint": "sha256:" + "f" * 64}
                        )
                    }
                )
            }
        )
        with pytest.raises(ValueError, match="fingerprint"):
            lab.procedures.submit_recovery(changed, request_key="changed-definition")

        recovery = lab.procedures.submit_recovery(plan, request_key="recover-analysis")
        assert recovery.id != source.id
        assert recovery.snapshot.recovery == plan.recovery
        recovery.resume()
        assert recovery.summary().outcome == "succeeded"
        assert (
            lab.procedures.submit_recovery(plan, request_key="recover-analysis").id
            == recovery.id
        )
        with (
            DaemonClient(independent_lab_daemon) as raw,
            pytest.raises(DaemonConflictError, match="different intent"),
        ):
            raw.submit_procedure(
                ProcedureSubmitCommand(
                    request_key="recover-analysis",
                    definition=plan.definition,
                    intent=plan.intent,
                    samples=plan.samples,
                    recovery=plan.recovery.model_copy(
                        update={"adapter_id": "different-adapter"}
                    ),
                )
            )
        assert {run.id for run in lab.runs().items} == run_ids
        assert (
            retained.measurements()["temperature"].require_values()
            == original_measurements
        )
        assert source.snapshot == original
        assert source.steps().model_dump_json() == original_history
        output = recovery.step("summary").output
        assert isinstance(output, AnalysisPublicationOutputRef)
        publication = retained.published_analysis(output.analysis_record_id)
        value = publication.fact("temperature").value
        assert isinstance(value, dict)
        assert value["unit"] == "K"
        kelvin = value["value"]
        assert isinstance(kelvin, float)
        assert kelvin > 0
        assert publication.inputs
        assert lab.setup.active() == setup
        assert lab.config.registry().entries == ()
