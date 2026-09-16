"""Exercise scalar IQ through author execution, HTTP reads, refresh and restart."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from scopecat.application.author_project import AuthorJobFailed
from scopecat.daemon.endpoint import DAEMON_URL_ENV
from scopecat_server.lifecycle import (  # noqa: TID251 - installed integration journey
    initialize_project,
    start_project,
    stop_project,
)

SOURCE = """from dataclasses import dataclass
from typing import Annotated

import numpy as np
import scopecat as sc


@dataclass(frozen=True)
class MeanData:
    iq: sc.DataRef[complex]


def shot_iq() -> Annotated[np.ndarray, sc.ArrayType(
    dtype="complex128", unit="V", dimensions=(sc.ArrayDimension("shot", 2),)
)]:
    return np.asarray([1 + 2j, 3 + 4j])


def mean_iq(samples) -> Annotated[complex, sc.ScalarType(sc.ComplexType(unit="V"))]:
    return complex(np.mean(samples))


@sc.experiment(id="mean-iq")
def mean_iq_experiment(experiment: sc.ExperimentContext) -> MeanData:
    samples = experiment.compute(fn=shot_iq)
    mean = experiment.compute(fn=mean_iq, samples=samples)
    return MeanData(mean)
"""


def check() -> None:
    os.environ.pop(DAEMON_URL_ENV, None)
    with tempfile.TemporaryDirectory(prefix="scopecat-scalar-iq-") as temporary:
        project = initialize_project(Path(temporary) / "project")
        source = project.root / "src/scopecat_lab/authored/signal.py"
        source.write_text(SOURCE, encoding="utf-8")
        start_project(project, timeout=90)
        try:
            with project.authoring() as session:
                run = session.prepare("mean-iq").run().wait(timeout=90).result()
                old_id = run.id
                variable = run.measurements()["iq"]
                assert variable.dtype == "complex128"
                assert variable.unit == "V"
                assert list(variable.require_values()) == [2 + 3j]
                source.write_text(
                    SOURCE.replace(
                        "return complex(np.mean(samples))",
                        "return complex(np.mean(samples)) + 1j",
                    ),
                    encoding="utf-8",
                )
                session.refresh()
                changed = session.prepare("mean-iq").run().wait(timeout=90).result()
                new_id = changed.id
                assert list(changed.measurements()["iq"].require_values()) == [2 + 4j]
                assert list(run.measurements()["iq"].require_values()) == [2 + 3j]
        finally:
            stop_project(project)
        start_project(project, timeout=90)
        try:
            with project.authoring() as session:
                for run_id, expected in ((old_id, 2 + 3j), (new_id, 2 + 4j)):
                    variable = session.run(run_id).measurements()["iq"]
                    assert variable.unit == "V"
                    assert list(variable.require_values()) == [expected]
                # Inject the reported assembly failure inside an isolated run
                # worker: the notebook exception must carry its actual cause.
                source.write_text(
                    SOURCE.replace(
                        "return complex(np.mean(samples))",
                        "from scopecat.execution import interpreter\n"
                        "    def fail(*args, **kwargs):\n"
                        "        raise TypeError(\n"
                        "            'unsupported persisted scalar: complex')\n"
                        "    interpreter.project_measurement_records = fail\n"
                        "    return complex(np.mean(samples))",
                    ),
                    encoding="utf-8",
                )
                session.refresh()
                failed = session.prepare("mean-iq").run()
                try:
                    failed.wait(timeout=90)
                except AuthorJobFailed as error:
                    message = str(error)
                else:
                    raise AssertionError("injected assembly failure was not reported")
                assert "TypeError: unsupported persisted scalar: complex" in message
        finally:
            stop_project(project)
    print("scalar IQ verified: units, reads, refresh, restart and notebook error cause")


if __name__ == "__main__":
    check()
