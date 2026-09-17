"""离线教材中的参数保存、分组分析与研究目录自动验收。"""

CONNECTION = """\
import json
import numpy as np
import scopecat as sc
from lab_teaching.parameters import Drive
from lab_teaching.session import open_parameters
from my_experiment.group_analysis import CurveSummary

project = sc.open_project()
session = project.authoring()
"""

GROUP_CELLS = (
    CONNECTION
    + """\
session.refresh()
from my_experiment.teaching import teaching_rabi
from scopecat.records.research_project import ResearchProjectEdit, RunHistoryFilter

params = open_parameters(session)
params[Drive]["q0"].frequency = 5.146
version = params.save(note="分组教学自动验收")
assert params.save() == version
assert open_parameters(session).version == version
request = teaching_rabi().sweep(amplitude=[0.12, 0.24]).sweep_parameter(
    Drive.frequency, "q0", np.linspace(5.135, 5.155, 21), name="frequency")
prepared = session.prepare(request, parameters=params)
assert prepared.preview.point_count == 42
run = prepared.run().wait(timeout=120).result()
assert params.version == version
fitted = session.analyze_groups_as(
    run.id, "my_experiment.group_analysis:summarize_curve", CurveSummary,
    by=("amplitude",), fitting="frequency")
assert len(fitted.groups) == 2
for group in fitted.groups:
    assert group.receipt.error is None
    assert group.value.status == "estimated"
    assert abs(group.value.peak_sample.to("GHz").value - 5.145) < 0.0011
    assert group.value.peak_frequency_uncertainty is None
    assert len(group.receipt.point_indices) == 21
    assert group.publication.dataset("curve").table.num_rows == 21
research = session.save_research_project(
    "teaching-groups", ResearchProjectEdit(name="教学分组"))
session.associate_research_member(research.id, "samples", "teaching-synthetic")
session.associate_research_member(research.id, "runs", run.id)
history = session.list_runs(history=RunHistoryFilter(research_project=research.id))
assert history.items[0].run_id == run.id
(project.root / "grouped-run.json").write_text(json.dumps({
    "run_id": run.id, "publication_id": fitted.publication.id,
    "parameter_version": version.name}), encoding="utf-8")
session.close()
""",
)

GROUP_REOPEN_CELLS = (
    CONNECTION
    + """\
from scopecat.records.research_project import RunHistoryFilter
bookmark = json.loads((project.root / "grouped-run.json").read_text(encoding="utf-8"))
restored = session.read_groups_as(
    bookmark["run_id"], bookmark["publication_id"], CurveSummary)
assert len(restored.groups) == 2
assert all(group.value.status == "estimated" for group in restored.groups)
assert len(session.run(bookmark["run_id"]).measurements()) == 42
history = session.list_runs(
    history=RunHistoryFilter(research_project="teaching-groups"))
assert history.items[0].run_id == bookmark["run_id"]
assert open_parameters(session)[Drive]["q0"].frequency == 5.146
session.close()
""",
)
