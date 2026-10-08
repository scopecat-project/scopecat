"""用户从逐 shot 改为平均 IQ 时的记录与重开回归。"""

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import numpy as np
import pytest
from numpy.typing import NDArray

import scopecat as sc
from lab_teaching.project import create_project
from scopecat_server.lifecycle import start_project, stop_project


@dataclass(frozen=True)
class TeachingRow[T]:
    amplitude: sc.Quantity
    iq: T


type MeanIQ = Annotated[complex, sc.ScalarType(sc.ComplexType(unit="ratio"))]


def test_teaching_can_record_mean_iq_without_losing_old_shots(
    tmp_path: Path, notebook_imports
):
    root = tmp_path / "mean-iq"
    create_project(root)
    project = sc.open_project(root)
    start_project(project, timeout=120)
    try:
        with project.authoring() as session:
            session.refresh()
            open_parameters = importlib.import_module(
                "my_experiment.setup"
            ).open_parameters
            params = open_parameters(session)
            notebook_imports.syspath_prepend(str(root / "src"))
            imported = importlib.import_module("my_experiment.teaching").teaching_rabi
            teaching_rabi = session.load_experiment(imported)
            old_request = teaching_rabi()
            old_request.values["amplitude"] = sc.Scan(np.linspace(0, 0.8, 7))
            raw = (
                session.prepare(old_request, parameters=params)
                .run()
                .wait(timeout=120)
                .result()
            )
            raw_id = raw.id
            shots = np.asarray(raw.measurements()["iq"].require_values())
            assert shots.shape == (7, 64)
            source = root / "src/my_experiment/teaching.py"
            text = source.read_text(encoding="utf-8").replace(
                "from my_experiment.response import response",
                "from my_experiment.response import response\n"
                "from numpy.typing import NDArray\n"
                "import numpy as np\n\n"
                "@sc.compute\n"
                "def mean_iq(iq: NDArray[np.complex128]) -> Annotated[\n"
                '    complex, sc.ScalarType(sc.ComplexType(unit="ratio"))\n'
                "]:\n"
                "    return complex(iq.mean())",
            )
            text = text.replace(
                '"iq": iq}',
                '"iq": mean_iq(iq)}',
            )
            source.write_text(text, encoding="utf-8")
            teaching_rabi = session.refresh(teaching_rabi)
            request = teaching_rabi()
            request.values["amplitude"] = sc.Scan(np.linspace(0, 0.8, 7))
            assert (
                request.declaration.code_revision
                != old_request.declaration.code_revision
            )
            assert session.prepare(
                old_request, parameters=params
            ).preview.code_revision == (old_request.declaration.code_revision)
            mean = (
                session.prepare(request, parameters=params)
                .run()
                .wait(timeout=120)
                .result()
            )
            mean_id = mean.id
            values = mean.measurements()["iq"]
            assert values.dtype == "complex128"
            assert values.unit == "ratio"
            np.testing.assert_allclose(values.require_values(), shots.mean(axis=1))
            rows = mean.result().rows_as(TeachingRow[MeanIQ])
            np.testing.assert_allclose([row.iq for row in rows], shots.mean(axis=1))
            assert all(row.amplitude.unit == "arb" for row in rows)
            with pytest.raises(TypeError, match="does not match"):
                raw.result().rows_as(TeachingRow[MeanIQ])
    finally:
        stop_project(project)
    start_project(project, timeout=120)
    try:
        with project.authoring() as session:
            session.refresh()
            np.testing.assert_array_equal(
                session.run(raw_id).measurements()["iq"].require_values(), shots
            )
            mean_rows = session.run(mean_id).result().rows_as(TeachingRow[MeanIQ])
            raw_rows = (
                session.run(raw_id)
                .result()
                .rows_as(TeachingRow[NDArray[np.complex128]])
            )
            np.testing.assert_allclose(
                [row.iq for row in mean_rows], shots.mean(axis=1)
            )
            np.testing.assert_array_equal([row.iq for row in raw_rows], shots)
    finally:
        stop_project(project)


@pytest.mark.parametrize("topic", ["refresh", "compute", None])
def test_shipped_editing_lessons(tmp_path: Path, monkeypatch, notebook_imports, topic):
    from nbclient import NotebookClient
    from nbformat import read, v4, write

    from lab_tools.notebook import kernel_command
    from lab_tools.verify_editing import (
        REFRESH_REOPEN_CELLS,
        editing_notebook,
        lesson_path,
        reopen_cells,
    )

    root = create_project(tmp_path / "修改 教材", topic=topic).parent
    material = (
        Path(__file__).resolve().parents[2]
        / "lab-teaching/src/lab_teaching/course_material"
    )
    expected = {
        "src/workspace_app.py": "lessons/workspace_app.py.txt",
        **{
            f"src/my_experiment/{name}.py": f"lessons/{name}.py.txt"
            for name in ("parameters", "response")
        },
        "src/my_experiment/setup.py": (
            "lessons/setup.py.txt"
            if topic is None
            else "lessons/parameters_setup.py.txt"
        ),
        "src/my_experiment/teaching.py": (
            "lessons/compute_experiment.py.txt"
            if topic == "compute"
            else "lessons/experiment.py.txt"
        ),
        "src/my_experiment/result_types.py": "result_types.py",
        "src/my_experiment/group_analysis.py": "group_analysis.py",
    }
    for generated, source in expected.items():
        assert (root / generated).read_bytes() == (material / source).read_bytes(), (
            f"Reinstall scopecat-lab-teaching: stale {generated}"
        )
    topics = ("refresh", "compute") if topic is None else (topic,)
    for name in topics:
        assert (
            lesson_path(root, name).read_bytes()
            == (material / f"lessons/{name}.ipynb").read_bytes()
        )
    if "refresh" in topics:
        extra = (
            (material / "lessons/experiment.py.txt")
            .read_text()
            .replace('id="teaching.rabi"', 'id="teaching.extra"')
            .replace("def teaching_rabi(", "def extra_rabi(")
        )
        assert (root / "examples/extra.py").read_bytes() == extra.encode()
    _, environment = kernel_command(root, source_path=False)
    monkeypatch.setenv("JUPYTER_PATH", environment["JUPYTER_PATH"])
    project = sc.open_project(root)

    def execute(name, document):
        start_project(project, timeout=120)
        try:
            NotebookClient(
                document,
                timeout=120,
                kernel_name="scopecat-lab",
                resources={"metadata": {"path": str(root / "notebooks")}},
            ).execute()
        finally:
            write(document, root / f"notebooks/verified-{name}.ipynb")
            stop_project(project)

    if topic is None:
        from lab_tools.verify_groups import GROUP_CHECKS, GROUP_REOPEN_CELLS
        from lab_tools.verify_groups import lesson_path as groups_path
        from lab_tools.verify_maintenance import ADD_ANALYSIS

        groups = read(groups_path(root), as_version=4)
        groups.cells.append(v4.new_code_cell(GROUP_CHECKS))
        execute("groups", groups)
    for name in topics:
        if name == "compute" and topic is None:
            (root / "src/my_experiment/teaching.py").write_bytes(
                (material / "lessons/compute_experiment.py.txt").read_bytes()
            )
        execute(name, editing_notebook(root, name))
        if name == "refresh":
            execute(
                "refresh-reopen",
                v4.new_notebook(
                    cells=[v4.new_code_cell(c) for c in REFRESH_REOPEN_CELLS]
                ),
            )
    if "compute" in topics:
        execute(
            "editing-reopen",
            v4.new_notebook(cells=[v4.new_code_cell(c) for c in reopen_cells(root)]),
        )
    if topic is None:
        execute(
            "maintenance",
            v4.new_notebook(
                cells=[
                    v4.new_code_cell(c)
                    for c in (*reopen_cells(root), *GROUP_REOPEN_CELLS, ADD_ANALYSIS)
                ]
            ),
        )
