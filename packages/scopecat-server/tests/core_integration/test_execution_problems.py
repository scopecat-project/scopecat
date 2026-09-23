from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import override

import pytest
from scopecat.kernel.errors import ProviderContractError, RunFailed, RunIndeterminate
from scopecat.kernel.graph_identity import ValueId
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.symbols import SymbolId
from scopecat.program.expressions import LiteralScalarExpr
from scopecat.program.logical import (
    LogicalEnsureState,
    LogicalStateAssignment,
    ValueDef,
)
from scopecat.records.measurement import MeasurementScalar
from scopecat.runs.parameter_evidence import (
    HOST_PARAMETER_EVIDENCE_KIND,
    read_host_parameter_evidence,
)
from scopecat.sdk.instruments import (
    DriverAcquisition,
    DriverOutcome,
    DriverReadback,
    DriverSuccess,
)
from scopecat_testkit.bound_program import ProgramFixture
from scopecat_testkit.server.execution import execute_bound_run
from scopecat_testkit.server.runtime import sqlite_run_repository
from scopecat_testkit.signal_instruments import TestSignalInstrument
from scopecat_testkit.workflow_fixtures import load_config, load_experiment


class FailingCollectInstrument(TestSignalInstrument):
    implementation_id = "test.failing_instrument"

    @override
    def collect(
        self,
        request: DriverAcquisition,
    ) -> DriverOutcome[DriverReadback]:
        del request
        raise RuntimeError("boom")


class UnexpectedResultInstrument(TestSignalInstrument):
    implementation_id = "test.unexpected_result_instrument"

    @override
    def collect(
        self,
        request: DriverAcquisition,
    ) -> DriverOutcome[DriverReadback]:
        return DriverSuccess(
            DriverReadback(
                values={
                    request.target.result("signal"): MeasurementScalar.create(
                        dtype="float64",
                        value=1.0,
                        unit="ratio",
                    ),
                    request.target.result("unexpected"): MeasurementScalar.create(
                        dtype="float64",
                        value=1.0,
                        unit="ratio",
                    ),
                }
            ),
        )


class InterruptingCollectInstrument(TestSignalInstrument):
    implementation_id = "test.interrupting_instrument"

    def __init__(self) -> None:
        super().__init__()
        self.aborted = False

    @override
    def collect(
        self,
        request: DriverAcquisition,
    ) -> DriverOutcome[DriverReadback]:
        del request
        raise KeyboardInterrupt("operator cancelled")

    @override
    def abort(self) -> None:
        self.aborted = True


class FailAfterFirstCollectInstrument(TestSignalInstrument):
    implementation_id = "test.fail_after_first_instrument"

    def __init__(self) -> None:
        super().__init__()
        self.collect_count = 0

    @override
    def collect(
        self,
        request: DriverAcquisition,
    ) -> DriverOutcome[DriverReadback]:
        self.collect_count += 1
        if self.collect_count > 1:
            raise RuntimeError("second collection failed")
        return super().collect(request)


def test_planning_rejects_missing_instrument(tmp_path: Path) -> None:
    with pytest.raises(ProviderContractError) as error:
        execute_bound_run(
            config=load_config(),
            experiment=load_experiment(),
            instruments=[],
            project_root=tmp_path,
        )

    assert "missing_instrument_description" in {
        problem.code for problem in error.value.problems
    }


def test_planning_rejects_unsupported_property(tmp_path: Path) -> None:
    experiment = load_experiment()
    selected_state = experiment.logical.program.bindings[0]
    assert isinstance(selected_state, LogicalStateAssignment)
    state = replace(selected_state, property_id="amplitude")
    experiment = replace(
        experiment,
        logical=replace(
            experiment.logical,
            program=replace(
                experiment.logical.program,
                effects=(state, *experiment.logical.program.acquisitions),
            ),
        ),
    )

    with pytest.raises(ProviderContractError) as error:
        execute_bound_run(
            config=load_config(),
            experiment=experiment,
            instruments=[TestSignalInstrument()],
            project_root=tmp_path,
        )

    assert "instrument_driver_unsupported_member" in {
        problem.code for problem in error.value.problems
    }


def test_planning_rejects_unsupported_acquisition_result(tmp_path: Path) -> None:
    experiment = load_experiment()
    acquisition = experiment.logical.program.acquisitions[0]
    unsupported_acquisition = replace(
        acquisition,
        results=(replace(acquisition.results[0], result_id="missing"),),
    )
    experiment = replace(
        experiment,
        logical=replace(
            experiment.logical,
            program=replace(
                experiment.logical.program,
                effects=tuple(
                    unsupported_acquisition if effect is acquisition else effect
                    for effect in experiment.logical.program.effects
                ),
            ),
        ),
    )

    with pytest.raises(ProviderContractError) as error:
        execute_bound_run(
            config=load_config(),
            experiment=experiment,
            instruments=[TestSignalInstrument()],
            project_root=tmp_path,
        )

    assert "instrument_acquisition_result_unsupported" in {
        problem.code for problem in error.value.problems
    }


def test_planning_rejects_acquisition_result_dtype_mismatch(tmp_path: Path) -> None:
    experiment = load_experiment()
    experiment = replace(
        experiment,
        bindings=replace(
            experiment.bindings,
            product_defs=(replace(experiment.bindings.product_defs[0], dtype="int64"),),
        ),
    )

    with pytest.raises(ProviderContractError) as error:
        execute_bound_run(
            config=load_config(),
            experiment=experiment,
            instruments=[TestSignalInstrument()],
            project_root=tmp_path,
        )

    assert "instrument_acquisition_result_dtype_mismatch" in {
        problem.code for problem in error.value.problems
    }


def test_instrument_exception_keeps_unknown_run(tmp_path: Path) -> None:
    with pytest.raises(RunIndeterminate) as error:
        execute_bound_run(
            config=load_config(),
            experiment=load_experiment(),
            instruments=[FailingCollectInstrument()],
            project_root=tmp_path,
        )

    assert "hardware_batch_unknown" in {
        problem.code for problem in error.value.problems
    }
    manifests = sqlite_run_repository(tmp_path).list_runs()
    assert len(manifests) == 1
    assert manifests[0].status == "unknown"
    assert manifests[0].outcome is not None
    assert manifests[0].outcome.result == "failed"
    assert manifests[0].outcome.certainty == "indeterminate"
    assert {problem.code for problem in manifests[0].outcome.problems} >= {
        "hardware_batch_unknown"
    }


def test_run_rejects_unexpected_instrument_results(tmp_path: Path) -> None:
    with pytest.raises(RunFailed) as error:
        execute_bound_run(
            config=load_config(),
            experiment=load_experiment(),
            instruments=[UnexpectedResultInstrument()],
            project_root=tmp_path,
        )

    assert error.value.problems[-1].code == "instrument_unexpected_product"
    manifest = sqlite_run_repository(tmp_path).list_runs()[0]
    assert manifest.status == "failed"


def _experiment_with_success_frequency(success_frequency: Quantity) -> ProgramFixture:
    experiment = load_experiment()
    [selected_state] = experiment.logical.program.bindings
    success_value = LiteralScalarExpr(success_frequency)
    success_value_id = ValueId(SymbolId(scope=("success_state",), local_id="frequency"))
    return replace(
        experiment,
        logical=replace(
            experiment.logical,
            program=replace(
                experiment.logical.program,
                value_defs=(
                    *experiment.logical.program.value_defs,
                    ValueDef(
                        id=success_value_id,
                        value_type=success_value.value_type,
                        source=success_value,
                    ),
                ),
                success_state=LogicalEnsureState(
                    (replace(selected_state, value_id=success_value_id),)
                ),
            ),
            scalar_values={
                **experiment.logical.scalar_values,
                success_value_id: success_value,
            },
        ),
    )


def test_success_state_evidence_has_base_scope_and_no_point_ordinal(
    tmp_path: Path,
) -> None:
    instrument = TestSignalInstrument()
    success_frequency = Quantity(91.0, "GHz")
    manifest = execute_bound_run(
        config=load_config(),
        experiment=_experiment_with_success_frequency(success_frequency),
        instruments=[instrument],
        project_root=tmp_path,
    )
    repository = sqlite_run_repository(tmp_path)
    records = repository.list_contents(
        manifest.run_id, limit=100, kind=HOST_PARAMETER_EVIDENCE_KIND
    ).items
    evidences = [
        read_host_parameter_evidence(repository, manifest.run_id, record.id).evidence
        for record in records
    ]
    [success] = [
        evidence for evidence in evidences if evidence.success_state is not None
    ]
    assert success.entries == ()
    assert success.success_state is not None
    assert success.success_state.parameter_scope == "base_configuration"
    assert success_frequency in [
        assignment.value for assignment in instrument.applied_requests[-1].scoped_values
    ]


def test_keyboard_interrupt_commits_interrupted_terminal_run(tmp_path: Path) -> None:
    instrument = InterruptingCollectInstrument()
    success_frequency = Quantity(91.0, "GHz")
    experiment = _experiment_with_success_frequency(success_frequency)
    with pytest.raises(KeyboardInterrupt, match="operator cancelled"):
        execute_bound_run(
            config=load_config(),
            experiment=experiment,
            instruments=[instrument],
            project_root=tmp_path,
        )

    repository = sqlite_run_repository(tmp_path)
    snapshot = repository.list_runs()[0]
    assert snapshot.status == "interrupted"
    assert all(
        read_host_parameter_evidence(
            repository, snapshot.run_id, record.id
        ).evidence.success_state
        is None
        for record in repository.list_contents(
            snapshot.run_id, limit=100, kind=HOST_PARAMETER_EVIDENCE_KIND
        ).items
    )
    [dataset] = repository.list_contents(
        snapshot.run_id,
        limit=100,
        role="dataset",
    ).items
    assert dataset.id == "raw-measurements"
    assert dataset.metadata == {
        "partial": True,
        "run_result": "cancelled",
        "run_certainty": "known",
        "expected_record_count": 3,
    }
    assert instrument.aborted
    [point_state] = instrument.applied_requests
    assert success_frequency not in point_state.values.values()
    assert snapshot.outcome is not None
    assert snapshot.outcome.result == "cancelled"
    assert "execution_interrupted" in {
        problem.code for problem in snapshot.outcome.problems
    }


def test_failed_run_publishes_committed_prefix_as_incomplete_dataset(
    tmp_path: Path,
) -> None:
    instrument = FailAfterFirstCollectInstrument()

    with pytest.raises(RunIndeterminate):
        execute_bound_run(
            config=load_config(),
            experiment=load_experiment(),
            instruments=[instrument],
            project_root=tmp_path,
        )

    repository = sqlite_run_repository(tmp_path)
    snapshot = repository.list_runs()[0]
    assert snapshot.status == "unknown"
    [dataset] = repository.list_contents(
        snapshot.run_id,
        limit=100,
        role="dataset",
    ).items
    assert dataset.metadata["partial"] is True
    assert dataset.metadata["expected_record_count"] == 3
