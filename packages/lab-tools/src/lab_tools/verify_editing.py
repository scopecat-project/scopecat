"""Execute shipped editing lessons, adding edits and assertions at learner stops."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .notebook_io import notebook_io

if TYPE_CHECKING:
    from nbformat import NotebookNode


def lesson_path(root: Path, topic: str) -> Path:
    path = root / f"notebooks/{topic}.ipynb"
    if not path.exists():
        path.write_bytes(
            files("lab_teaching.course_material")
            .joinpath(f"lessons/{topic}.ipynb")
            .read_bytes()
        )
    if topic == "refresh":
        extra = root / "examples/extra.py"
        if not extra.exists():
            extra.parent.mkdir(exist_ok=True)
            text = (
                files("lab_teaching.course_material")
                .joinpath("lessons/experiment.py.txt")
                .read_text(encoding="utf-8")
            )
            extra.write_text(
                text.replace('id="teaching.rabi"', 'id="teaching.extra"').replace(
                    "def teaching_rabi(", "def extra_rabi("
                ),
                encoding="utf-8",
            )
    return path


def editing_notebook(root: Path, topic: str) -> NotebookNode:
    """Retain original cells; automate only the documented source/number edits."""
    nb = notebook_io()
    document = nb.read(lesson_path(root, topic), as_version=4)
    cells = cast("list[NotebookNode]", document.cells)
    if topic == "refresh":
        cells.insert(5, nb.v4.new_code_cell(REFRESH_EDIT))
        cells.append(nb.v4.new_code_cell(REFRESH_CHECKS))
        cells.append(nb.v4.new_code_cell(SYNTAX_CHECK))
        cells.append(nb.v4.new_code_cell(REFRESH_RECOVER))
    elif topic == "compute":
        # The selection is the one learner-edited cell, not an implicit latest run.
        cells[8] = nb.v4.new_code_cell("number = session.run_number(mean)")
        cells.append(nb.v4.new_code_cell(COMPUTE_CHECKS))
        cells.append(nb.v4.new_code_cell(COMPUTE_FAILURE))
        cells.append(nb.v4.new_code_cell(COMPUTE_RECOVER))
    else:
        raise ValueError(topic)
    return document


REFRESH_EDIT = """\
import numpy as np
project = sc.open_project()
source = project.root / "src/my_experiment/teaching.py"
original = source.read_text(encoding="utf-8")
assert before.values["seed"] == 200
old_prepared = session.prepare(before.sweep(amplitude=[0.1, 0.2]), parameters=params)
old_revision = old_prepared.preview.code_revision
source.write_text(original.replace("seed: int = 200", "seed: int = 400"),
                  encoding="utf-8")
"""

REFRESH_CHECKS = """\
assert sc.notebook() is session
assert before.values["seed"] == 200
assert after.values["seed"] == teaching_rabi().values["seed"] == 400
assert old_prepared.preview.code_revision == old_revision
assert prepared.preview.code_revision != old_revision
assert session.prepare(before, parameters=params).preview.code_revision == old_revision
old_run = old_prepared.run().wait(timeout=120).result()
assert not np.array_equal(old_run.measurements()["iq"].require_values(),
                          run.measurements()["iq"].require_values())
assert new_request.declaration.id == "teaching.extra"
np.testing.assert_array_equal(old_run.measurements()["iq"].require_values(),
                              new_run.measurements()["iq"].require_values())
extra_original = extra.read_text(encoding="utf-8")
assert sc.notebook(live=False) is session
extra.write_text(extra_original.replace("seed: int = 200", "seed: int = 401"),
                 encoding="utf-8")
assert extra_experiments.extra_rabi().values["seed"] == 200
assert sc.notebook(live=True) is session
assert extra_experiments.extra_rabi().values["seed"] == 401
source.write_text(original + "\\ninvalid python !\\n", encoding="utf-8")
"""

SYNTAX_CHECK = """\
from scopecat.daemon.preparation import AuthorPreparationFailed
try:
    teaching_rabi()
except AuthorPreparationFailed as error:
    assert "SyntaxError" in str(error), str(error)
else:
    raise AssertionError("invalid saved source silently used the old alias")
source.write_text(original.replace("seed: int = 200", "seed: int = 400"),
                  encoding="utf-8")
"""

REFRESH_RECOVER = """\
assert teaching_rabi().values["seed"] == 400
assert before.values["seed"] == 200
# Executing the same retained preparation again must still use seed 200.
old_again = old_prepared.run().wait(timeout=120).result()
np.testing.assert_array_equal(old_run.measurements()["iq"].require_values(),
                              old_again.measurements()["iq"].require_values())
import json
np.save(project.root / "refresh-shots.npy",
        old_run.measurements()["iq"].require_values(), allow_pickle=False)
(project.root / "refresh-run.json").write_text(json.dumps({
    "id": old_run.id, "number": session.run_number(old_run),
}), encoding="utf-8")
session.close()
"""

COMPUTE_CHECKS = """\
import json
project = sc.open_project()
shots = np.asarray(raw.measurements()["iq"].require_values())
assert shots.shape == (7, 64)
assert all(row.amplitude.unit == "arb" for row in rows)
assert mean.measurements()["iq"].unit == "ratio"
try:
    raw.result().rows_as(MeanRow)
except TypeError as error:
    assert "does not match" in str(error), str(error)
else:
    raise AssertionError("shot arrays must not read as scalar IQ")
np.testing.assert_array_equal(raw.measurements()["iq"].require_values(), shots)
np.save(project.root / "editing-shots.npy", shots, allow_pickle=False)
(project.root / "editing-runs.json").write_text(json.dumps({
    "shots": raw.id, "mean": mean.id, "number": number,
}), encoding="utf-8")
source = project.root / "src/my_experiment/teaching.py"
working = source.read_text(encoding="utf-8")
assert "return complex(iq.mean())" in working
source.write_text(working.replace("return complex(iq.mean())",
                  'raise ValueError("teaching mean deliberate failure")'),
                  encoding="utf-8")
"""

COMPUTE_FAILURE = """\
from scopecat.application.author_project import AuthorJobFailed
failed_job = session.prepare(experiments.mean_rabi(), parameters=params).run()
try:
    failed_job.wait(timeout=120)
except AuthorJobFailed as error:
    assert "teaching mean deliberate failure" in str(error), str(error)
else:
    raise AssertionError("deliberate compute failure must reach the notebook")
source.write_text(working, encoding="utf-8")
"""

COMPUTE_RECOVER = """\
repaired = session.prepare(experiments.mean_rabi().sweep(amplitude=scan),
                           parameters=params).run().wait(timeout=120).result()
np.testing.assert_allclose([row.iq for row in repaired.result().rows_as(MeanRow)],
                           shots.mean(axis=1))
session.close()
"""


def reopen_cells(root: Path) -> tuple[str, ...]:
    """Use the shipped connection, history and read cells in a fresh kernel."""
    document = notebook_io().read(lesson_path(root, "compute"), as_version=4)
    cells = cast("list[NotebookNode]", document.cells)
    return (
        cast("str", cells[1].source),
        cast("str", cells[7].source),
        REOPEN_SELECTION,
        cast("str", cells[9].source),
        REOPEN_CHECKS,
        cast("str", cells[9].source),
        (
            "assert {r.run_id for r in session.list_runs().items} == before_runs\n"
            "session.close()\n"
        ),
    )


REOPEN_SELECTION = """\
import json
import numpy as np
project = sc.open_project()
ids = json.loads((project.root / "editing-runs.json").read_text(encoding="utf-8"))
# Stand in for choosing the previously noted exact number from displayed history.
number = ids["number"]
before_runs = {r.run_id for r in session.list_runs().items}
assert "experiments" not in globals()
assert "IQ" not in globals()
"""

REOPEN_CHECKS = """\
assert session.run(number).id == ids["mean"]
shots = np.load(project.root / "editing-shots.npy", allow_pickle=False)
np.testing.assert_array_equal(
    session.run(ids["shots"]).measurements()["iq"].require_values(), shots)
np.testing.assert_allclose([row.iq for row in reopened], shots.mean(axis=1))
try:
    session.run(ids["shots"]).result().rows_as(MeanRow)
except TypeError as error:
    assert "does not match" in str(error), str(error)
else:
    raise AssertionError("shot arrays must not read as scalar IQ after restart")
np.testing.assert_array_equal(
    session.run(ids["shots"]).measurements()["iq"].require_values(), shots)
assert {r.run_id for r in session.list_runs().items} == before_runs
"""


REFRESH_REOPEN_CELLS = (
    "import scopecat as sc\nsession = sc.notebook()\nsession.history()",
    """\
import json
import numpy as np
project = sc.open_project()
bookmark = json.loads((project.root / "refresh-run.json").read_text(encoding="utf-8"))
before_runs = {r.run_id for r in session.list_runs().items}
for _ in range(2):
    retained = session.run(bookmark["number"])
    assert retained.id == bookmark["id"]
    np.testing.assert_array_equal(retained.measurements()["iq"].require_values(),
        np.load(project.root / "refresh-shots.npy", allow_pickle=False))
assert {r.run_id for r in session.list_runs().items} == before_runs
session.close()
""",
)
