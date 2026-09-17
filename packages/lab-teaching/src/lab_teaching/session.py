"""Small course conveniences; scientific analysis remains a retained author step."""

from dataclasses import dataclass
from typing import TypedDict, cast

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure

from scopecat.api.parameters import ParameterWorkspace
from scopecat.api.run import RunHandle
from scopecat.application.author_project import AuthorProject
from scopecat.daemon.client import DaemonNotFoundError
from scopecat.daemon.wire import SampleCreateCommand
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.sample import SampleRevisionDraft, SampleSelector

from .parameters import Drive


class _DiagnosticValue(TypedDict):
    status: str
    selected_value: float | None
    failure: str | None


@dataclass(frozen=True)
class RabiReport:
    run_id: str
    analysis_id: str
    status: str
    pi_amplitude: float | None
    message: str
    figure: Figure


def open_parameters(session: AuthorProject) -> ParameterWorkspace:
    """Initialize a teaching-only sample/context once, then reopen its baseline."""
    name = "teaching-start"
    try:
        _ = session.config.entry(name)
    except DaemonNotFoundError:
        sample_id = "teaching-synthetic"
        try:
            sample = session.get_sample(sample_id)
        except DaemonNotFoundError:
            _ = session.create_sample(
                SampleCreateCommand(
                    operation_id="teaching-sample-initialization",
                    sample_id=sample_id,
                    kind="synthetic",
                    actor="teaching",
                    content=SampleRevisionDraft(display_name="独立合成教学样品"),
                )
            )
            sample = session.get_sample(sample_id)
        active = session.config.active()
        _ = session.config.save_context(
            entry_id=name,
            base=ConfigContextRef(
                entry_id=active.entry.id, content_hash=active.entry.content_hash
            ),
            sample=SampleSelector(
                sample_id=sample_id, revision=sample.record.active_revision
            ),
            working_point_id="teaching",
            label="教学起点(合成响应)",
        )
    try:
        return session.config.workspace(context="teaching-table", latest=True)
    except DaemonNotFoundError:
        pass
    params = session.config.workspace(context=name)
    if "teaching_drive" not in params:
        table = params.declare_table(Drive)
        _ = table.add(Drive(id="q0", frequency=5.15))
    _ = params.save("teaching-table", note="教学输入; 非测量结果")
    return params


def analyze_rabi(session: AuthorProject, run: RunHandle) -> RabiReport:
    """Run the supplied registered analysis on the acquisition's source revision."""
    revision = AuthorRevisionRef(
        content_hash=cast("str", run.request.metadata["author_code_revision"])
    )
    receipt = session.analyze(
        run.id,
        "lab_teaching.analysis:rabi_diagnostic",
        code_revision=revision,
    )
    result = cast(
        "_DiagnosticValue",
        cast(
            "object",
            run.published_analysis(receipt.analysis_id).fact("diagnostic").value,
        ),
    )
    measurements = run.measurements()
    amplitudes = np.asarray(
        [
            float(cast("float", value))
            for value in measurements["amplitude"].require_values()
        ]
    )
    centers = np.asarray(
        [
            np.mean(np.asarray(row, dtype=np.complex128)).real
            for row in measurements["iq"].require_values()
        ]
    )
    figure, axes = plt.subplots()
    _ = axes.plot(amplitudes, centers, ".-")  # pyright: ignore[reportUnknownMemberType] - matplotlib overloads lack complete types
    _ = axes.set(
        xlabel="Amplitude (arb)",
        ylabel="Mean I (ratio)",
        title=f"{run.id}: {result['status']}",
    )
    return RabiReport(
        run.id,
        receipt.analysis_id,
        result["status"],
        result["selected_value"],
        result["failure"]
        or ("获得教学候选; 请用新的 seed 独立采集验证。尚未接受或发布参数。"),
        figure,
    )
