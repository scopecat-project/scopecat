from collections.abc import Iterator

import pytest
from scopecat_testkit.instrument_host import TestRunInstrumentHost

from scopecat.execution.effect_interpreter import RunEffectInterpreter
from scopecat.execution.program import (
    RunCoverageCheckpoint,
    RunCoveredOperation,
    RunHostParameterEvidence,
)
from scopecat.kernel.point_identity import LogicalPointId, PointDomainId
from scopecat.kernel.points import AcceptedRunPoint
from scopecat.records.parameter_read import (
    HostParameterEvidence,
    HostPointParameterRead,
    ScalarExpressionReadEvidence,
)


@pytest.mark.parametrize("fail_write", [False, True])
def test_host_evidence_is_durable_before_consuming_further_effects(
    fail_write: bool,
) -> None:
    events: list[str] = []
    evidence = HostParameterEvidence(
        entries=(
            HostPointParameterRead(
                point_ordinal=0, evidence=ScalarExpressionReadEvidence()
            ),
        ),
        binding=(),
    )

    def publish(value: HostParameterEvidence) -> None:
        assert value == evidence
        events.append("publish")
        if fail_write:
            raise RuntimeError("evidence storage unavailable")

    def coverage() -> Iterator[RunCoveredOperation]:
        yield RunHostParameterEvidence(evidence)
        events.append("next operation")
        yield RunCoverageCheckpoint("point-0", (0,))

    result = RunEffectInterpreter(
        run_id="host-evidence",
        coordinate_ids=(),
        instruments=TestRunInstrumentHost(),
        publish_host_parameter_evidence=publish,
    ).run(
        coverage(),
        points=(
            AcceptedRunPoint(LogicalPointId(PointDomainId("test", "root"), 0), {}),
        ),
    )

    if fail_write:
        assert events == ["publish"]
        assert [problem.code for problem in result.problems] == [
            "run_effect_interpretation_failed"
        ]
    else:
        assert events == ["publish", "next operation"]
        assert not result.problems
