"""Evidence appended to the shipped grouping lesson, never a parallel lesson."""

from importlib.resources import files
from pathlib import Path


def lesson_path(root: Path) -> Path:
    """Supply the topic notebook to the standalone default-course verifier."""
    path = root / "notebooks/groups.ipynb"
    if not path.exists():
        path.write_bytes(
            files("lab_teaching.course_material")
            .joinpath("lessons/groups.ipynb")
            .read_bytes()
        )
    return path


# Checks stay outside the learner's notebook. The bookmark is verifier evidence,
# not an additional step learners need to read their published results.
GROUP_CHECKS = """\
import json
project = sc.open_project()
assert prepared.preview.point_count == 42
assert len(run.measurements()) == 42
assert len(fitted.groups) == len(restored.groups) == 2
for group in fitted.groups:
    assert group.receipt.error is None
    assert group.value.status == "estimated"
    assert abs(group.value.peak_sample.to("GHz").value - 5.145) < 0.0011
    assert group.value.peak_frequency_uncertainty is None
    assert len(group.receipt.point_indices) == 21
    assert group.publication.dataset("curve").table.num_rows == 21
assert [g.value for g in restored.groups] == [g.value for g in fitted.groups]
original_shots = np.asarray(run.measurements()["iq"].require_values()).copy()
original_curves = [g.publication.dataset("curve").table for g in fitted.groups]
original_values = [g.value for g in fitted.groups]
original_analyses = run.analysis_summaries()
before_runs = {item.run_id for item in session.list_runs().items}

# Perform the final Markdown exercise: add an amplitude and analyze its own run.
changed_request = teaching_rabi().sweep(amplitude=[0.12, 0.24, 0.36]).sweep_parameter(
    Drive.frequency, "q0", np.linspace(5.135, 5.155, 21), name="frequency")
changed_prepared = session.prepare(changed_request, parameters=params)
assert changed_prepared.preview.point_count == 63
changed_run = changed_prepared.run().wait(timeout=120).result()
changed = session.analyze_groups_as(
    changed_run.id, "my_experiment.group_analysis:summarize_curve", CurveSummary,
    by=("amplitude",), fitting="frequency")
assert changed_run.id != run.id
# Publication IDs are scoped to their run, not globally unique.
assert (changed_run.id, changed.publication.id) != (run.id, fitted.publication.id)
assert len(changed_run.measurements()) == 63
assert len(changed.groups) == 3
assert all(g.receipt.error is None and g.value.status == "estimated"
           and len(g.receipt.point_indices) == 21
           and g.publication.dataset("curve").table.num_rows == 21
           for g in changed.groups)
assert {item.run_id for item in session.list_runs().items} == (
    before_runs | {changed_run.id})
old = session.read_groups_as(run.id, fitted.publication.id, CurveSummary)
assert [g.value for g in old.groups] == original_values
assert run.analysis_summaries() == original_analyses
assert all(g.publication.dataset("curve").table.equals(table)
           for g, table in zip(old.groups, original_curves, strict=True))
np.testing.assert_array_equal(run.measurements()["iq"].require_values(), original_shots)
np.save(project.root / "grouped-shots.npy", original_shots, allow_pickle=False)
(project.root / "grouped-run.json").write_text(json.dumps({
    "run_id": run.id, "publication_id": fitted.publication.id,
    "changed_run_id": changed_run.id,
    "curves": [table.to_pylist() for table in original_curves],
    "values": [repr(value) for value in original_values],
}), encoding="utf-8")
session.close()
"""

GROUP_REOPEN_CELLS = (
    """\
import json
import numpy as np
import scopecat as sc
project = sc.open_project()
session = sc.notebook()
from my_experiment.group_analysis import CurveSummary
bookmark = json.loads((project.root / "grouped-run.json").read_text(encoding="utf-8"))
before_runs = {item.run_id for item in session.list_runs().items}
assert {bookmark["run_id"], bookmark["changed_run_id"]} <= before_runs
retained_run = session.run(bookmark["run_id"])
before_analyses = retained_run.analysis_summaries()
restored = session.read_groups_as(
    bookmark["run_id"], bookmark["publication_id"], CurveSummary)
assert len(restored.groups) == 2
assert [repr(g.value) for g in restored.groups] == bookmark["values"]
assert [g.publication.dataset("curve").table.to_pylist()
        for g in restored.groups] == bookmark["curves"]
assert retained_run.analysis_summaries() == before_analyses
assert len(retained_run.measurements()) == 42
np.testing.assert_array_equal(
    retained_run.measurements()["iq"].require_values(),
    np.load(project.root / "grouped-shots.npy", allow_pickle=False))
assert {item.run_id for item in session.list_runs().items} == before_runs
session.close()
""",
)
