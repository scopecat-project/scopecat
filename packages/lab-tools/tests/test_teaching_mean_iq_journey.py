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
from lab_teaching.session import open_parameters
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
                "from lab_teaching.synthetic import response",
                "from lab_teaching.synthetic import response\n"
                "from numpy.typing import NDArray\n"
                "import numpy as np\n\n"
                "@sc.compute\n"
                "def mean_iq(iq: NDArray[np.complex128]) -> Annotated[\n"
                '    complex, sc.ScalarType(sc.ComplexType(unit="ratio"))\n'
                "]:\n"
                "    return complex(iq.mean())",
            )
            text = text.replace("iq: sc.ProductRef", "iq: sc.DataRef[complex]")
            text = text.replace(
                'cast("sc.ProductRef", iq))',
                'mean_iq(cast("sc.ProductRef", iq)))',
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
