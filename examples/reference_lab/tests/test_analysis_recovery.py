"""A real daemon retains acquisition across an explicit new analysis procedure."""

import shutil
from collections.abc import Generator
from pathlib import Path

import pytest
from scopecat.automation import ProcedureSubmitCommand
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.project import load_project
from scopecat_server.lifecycle import start_project, stop_project

from reference_lab.configuration import EXAMPLE_ROOT


@pytest.fixture(scope="module")
def reference_lab_daemon(tmp_path_factory: pytest.TempPathFactory) -> Generator[str]:
    root = tmp_path_factory.mktemp("analysis-recovery")
    for name in ("config", "src"):
        shutil.copytree(EXAMPLE_ROOT / name, root / name)
    shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", root / "scopecat.toml")
    project = load_project(root / "scopecat.toml")
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("SCOPECAT_DAEMON_URL", raising=False)
        endpoint = start_project(project)
    try:
        yield endpoint.base_url
    finally:
        stop_project(project)


def test_failed_analysis_recovers_without_reacquisition(reference_lab_daemon: str):
    from reference_lab.application import create_application
    from reference_lab.workflows.analysis_recovery import (
        TEMPERATURE_ANALYSIS_RECOVERY,
        failed_temperature_analysis,
    )
    from reference_lab.workflows.temperature_diagnostic import (
        TemperatureDiagnosticIntent,
    )

    with create_application(Path(EXAMPLE_ROOT)).connect(reference_lab_daemon) as lab:
        source = lab.procedures.submit(
            failed_temperature_analysis,
            TemperatureDiagnosticIntent(initial_config=lab.config.active().config),
            request_key="known-software-failure",
        )
        with pytest.raises(ValueError, match="demonstration software analysis failure"):
            source.resume()
        original = source.snapshot
        original_history = source.steps().model_dump_json()
        assert original.closure.status == "failed"
        acquired = source.step("sample").output
        retained = lab.get_run(acquired.run_id)
        original_measurements = retained.measurements()["temperature"].require_values()
        run_ids = {run.id for run in lab.runs().items}
        assert len(run_ids) == 1

        available = lab.procedures.recovery_availability(
            TEMPERATURE_ANALYSIS_RECOVERY, source.id
        )
        assert available.reason is None
        plan = available.plan
        assert plan is not None
        assert plan.recovery.retained_run == acquired
        assert plan.recovery.definition == original.definition
        with DaemonClient(reference_lab_daemon) as raw:
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
        assert {run.id for run in lab.runs().items} == run_ids
        assert (
            retained.measurements()["temperature"].require_values()
            == original_measurements
        )
        assert source.snapshot == original
        assert source.steps().model_dump_json() == original_history
        output = recovery.step("summary").output
        publication = retained.published_analysis(output.analysis_record_id)
        assert publication.fact("temperature").value["kelvin"] > 0
        assert publication.inputs
