"""Small course conveniences; scientific analysis remains a retained author step."""

from dataclasses import dataclass
from typing import TypedDict, cast

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure

import scopecat as sc
from scopecat.api.parameter_revisions import BranchParameterEditor
from scopecat.api.run import RunHandle
from scopecat.application.author_project import AuthorProject
from scopecat.daemon.client import DaemonNotFoundError
from scopecat.records.author_revision import AuthorRevisionRef

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


def open_parameters(session: AuthorProject) -> BranchParameterEditor:
    """Open the teaching parameter branch without creating a sample or working point."""
    name = "teaching-table"
    try:
        session.use(parameter_branch=name)
    except DaemonNotFoundError:
        initial = session.parameters.save(
            name="teaching-initial",
            catalog=sc.parameter_catalog("teaching", Drive),
            parameters=sc.parameter_snapshot(
                "teaching-inputs",
                tables={Drive: [Drive(id="q0", frequency=5.15)]},
            ),
            note="教学输入; 非测量结果",
        )
        session.parameters.create_branch(name, revision=initial)
        session.use(parameter_branch=name)
    return session.params


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
