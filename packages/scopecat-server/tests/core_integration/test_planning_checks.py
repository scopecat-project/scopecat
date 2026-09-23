from __future__ import annotations

from pathlib import Path
from typing import Literal, Never

import pytest
import scopecat as sc
import scopecat.planning.system as planning_system
import scopecat_testkit.planning as test_planning
from scopecat.config.candidates import CandidateConfig
from scopecat.config.changes import parameter_change_proposal_from_updates
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.problems import (
    Problem,
    ProblemPhase,
    problem,
)
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.planning.check_results import ExperimentCheckResult
from scopecat.program.domain import domain_program
from scopecat.records.config import (
    ConfigProfileSnapshot,
    DomainTargetBinding,
    config_content_hash,
)
from scopecat.sdk.domain.batch import (
    DomainBatchRequest,
)
from scopecat.sdk.domain.compiler import (
    DomainBatchCandidate,
    DomainBatchPreparationCost,
    DomainBatchPreparationLimits,
)
from scopecat.sdk.domain.execution import PreparedDomainExecution
from scopecat_testkit.authoring import simple_experiment
from scopecat_testkit.domain import domain_call
from scopecat_testkit.instrument_host import compose_test_instruments
from scopecat_testkit.server.in_process_lab import (
    InProcessLab,
    InProcessPreparedExperiment,
    in_process_lab,
)
from scopecat_testkit.signal_instruments import TestSignalInstrumentProvider
from scopecat_testkit.workflow_fixtures import load_config, load_invocation


def _lab(
    tmp_path: Path,
    *,
    config: ConfigProfileSnapshot | None = None,
) -> InProcessLab:
    selected_config = load_config() if config is None else config
    composition = compose_test_instruments(
        config=selected_config,
        provider=TestSignalInstrumentProvider(),
    )
    return in_process_lab(
        tmp_path,
        config=selected_config,
        system=composition.system,
        instrument_backend=composition.backend,
    )


def _planning_failure(code: str) -> CheckFailed:
    return CheckFailed(
        (
            problem(
                code,
                "injected planning failure",
                phase=ProblemPhase.PLANNING,
            ),
        )
    )


def _assert_terminal_problem(
    prepared: InProcessPreparedExperiment,
    *,
    terminal: Literal["check", "preview"],
    code: str,
) -> None:
    if terminal == "check":
        report = prepared.check()
        assert not report.ok
        assert report.preview is None
        assert [problem.code for problem in report.problems] == [code]
        return

    with pytest.raises(CheckFailed) as captured:
        prepared.preview()
    assert [problem.code for problem in captured.value.problems] == [code]


class _RejectingDomainCompiler:
    def __init__(self) -> None:
        self.compile_calls = 0

    @property
    def target_id(self) -> str:
        return "tests.domain.target"

    @property
    def target_kind(self) -> str:
        return "tests.domain"

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        return ()

    def initial_batch_preparation_limits(
        self,
        point_count: int,
    ) -> DomainBatchPreparationLimits:
        return DomainBatchPreparationLimits(
            max_points=point_count,
            max_retained_bytes=1024 * 1024,
        )

    def prepare_batch(self, request: DomainBatchRequest) -> DomainBatchCandidate:
        return DomainBatchCandidate(
            compatible_point_count=len(request.points),
            preparation_cost=DomainBatchPreparationCost(
                analyzed_point_count=len(request.points),
                retained_bytes=0,
            ),
            _compile=self._compile_batch,
        )

    def _compile_batch(
        self,
        request: DomainBatchRequest,
    ) -> PreparedDomainExecution:
        del request
        self.compile_calls += 1
        raise _planning_failure("injected_domain_compile_batch_failure")


def _domain_invocation() -> sc.ExperimentInvocation:
    program = domain_program(
        "check-program",
        dialect_id="test",
        dialect_version="1",
        body=object(),
    )

    @sc.module(id="test.check-domain")
    def module(context: sc.ModuleContext) -> None:
        context.use(domain_call(program))

    @sc.experiment(id="test.check-domain", kind="check-domain")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(module())

    return experiment.build()


def test_prepared_check_returns_preview_when_successful(tmp_path: Path) -> None:
    lab = _lab(tmp_path)

    report = lab.prepare(load_invocation()).check()

    assert report.ok
    assert report.problems == ()
    assert report.preview is not None
    assert report.preview.point_count == 3


@pytest.mark.parametrize("terminal", ["check", "preview"])
def test_check_and_preview_surface_local_materialization_errors(
    terminal: Literal["check", "preview"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_local_materialization(
        *_args: object,
        **_kwargs: object,
    ) -> Never:
        raise _planning_failure("injected_local_materialization_failure")

    monkeypatch.setattr(
        planning_system,
        "materialize_local_execution",
        reject_local_materialization,
    )

    _assert_terminal_problem(
        _lab(tmp_path).prepare(load_invocation()),
        terminal=terminal,
        code="injected_local_materialization_failure",
    )


@pytest.mark.parametrize("terminal", ["check", "preview"])
def test_check_and_preview_surface_first_domain_batch_errors(
    terminal: Literal["check", "preview"],
    tmp_path: Path,
) -> None:
    compiler = _RejectingDomainCompiler()
    config = load_config()
    config = config.model_copy(
        update={
            "system": config.system.model_copy(
                update={
                    "domain_target": DomainTargetBinding(
                        id=compiler.target_id,
                        kind=compiler.target_kind,
                    )
                }
            )
        }
    )
    prepared = _lab(tmp_path, config=config).prepare(
        _domain_invocation(),
        system=sc.ExperimentSystem(
            instrument_catalog=InstrumentContractCatalog(
                config_content_hash=config_content_hash(config)
            ),
            domain_compiler=compiler,
        ),
    )
    _assert_terminal_problem(
        prepared,
        terminal=terminal,
        code="injected_domain_compile_batch_failure",
    )
    assert compiler.compile_calls == 1


def test_prepared_check_returns_configuration_problems_without_preview(
    tmp_path: Path,
) -> None:
    config = load_config()
    invalid_config = config.model_copy(
        update={
            "system": config.system.model_copy(
                update={
                    "routing": config.system.routing.model_copy(
                        update={
                            "routes": [
                                config.system.routing.routes[0].model_copy(
                                    update={"instrument_id": "missing"}
                                )
                            ]
                        }
                    )
                }
            )
        }
    )
    lab = _lab(tmp_path, config=invalid_config)

    report = lab.prepare(load_invocation()).check()

    assert not report.ok
    assert {problem.phase for problem in report.problems} == {
        ProblemPhase.CONFIGURATION
    }
    assert report.preview is None


def test_check_report_rejects_preview_with_problems(tmp_path: Path) -> None:
    blocking = Problem(
        code="test_error",
        phase=ProblemPhase.AUTHORING,
        message="test error",
    )

    successful = _lab(tmp_path).prepare(load_invocation()).check()
    assert successful.preview is not None
    with pytest.raises(ValueError, match="successful experiment check"):
        ExperimentCheckResult(
            problems=(blocking,),
            preview=successful.preview,
        )


def test_check_result_rejects_success_without_preview() -> None:
    with pytest.raises(ValueError, match="successful experiment check"):
        ExperimentCheckResult(problems=(), preview=None)


@pytest.mark.parametrize(
    "terminal",
    ["check", "preview", "run"],
)
def test_session_candidate_config_is_not_read_before_authoring(
    terminal: Literal["check", "preview", "run"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_reads = 0

    def unexpected_candidate_read(*_args: object, **_kwargs: object) -> object:
        nonlocal candidate_reads
        candidate_reads += 1
        raise AssertionError("candidate config must not be read for invalid authoring")

    monkeypatch.setattr(
        test_planning,
        "resolve_candidate_config_snapshot",
        unexpected_candidate_read,
    )
    config = load_config()
    candidate = CandidateConfig(
        parameter_proposal=parameter_change_proposal_from_updates(
            source_run_id="not-read",
            source_config=config,
            analysis_title="not read",
            analysis_record_id="analysis-not-read",
            proposal_id="not-read",
            updates=(
                sc.replace_scalar_parameter(
                    "drive_frequency",
                    sc.Quantity(5.1, "GHz"),
                ),
            ),
            reason="must not be resolved",
            confidence=None,
        ),
    )
    lab = _lab(tmp_path, config=config)
    prepared = lab.prepare(simple_experiment().bind(), config=candidate)

    if terminal == "check":
        assert prepared.check().problems[0].code == ("experiment_missing_input")
    else:
        method = prepared.preview if terminal == "preview" else prepared.run
        with pytest.raises(CheckFailed) as error:
            method()
        assert error.value.problems[0].code == ("experiment_missing_input")

    assert candidate_reads == 0
