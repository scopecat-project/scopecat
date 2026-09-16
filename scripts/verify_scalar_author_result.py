"""Exercise scalar IQ through author execution, HTTP reads, refresh and restart."""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path
from typing import cast

from scopecat.application.author_project import AuthorJobFailed
from scopecat.authoring.experiments import Experiment
from scopecat.daemon.endpoint import DAEMON_URL_ENV
from scopecat.daemon.preparation import AuthorPreparationFailed
from scopecat_server.lifecycle import (  # noqa: TID251 - installed integration journey
    initialize_project,
    start_project,
    stop_project,
)

SOURCE = """from dataclasses import dataclass
from typing import Annotated

import numpy as np
from numpy.typing import NDArray
import scopecat as sc


@dataclass(frozen=True)
class MeanData:
    iq: sc.DataRef[complex]


@sc.compute
def shot_iq() -> Annotated[NDArray[np.complex128], sc.ArrayType(
    dtype="complex128", unit="V", dimensions=(sc.ArrayDimension("shot", 2),)
)]:
    return np.asarray([1 + 2j, 3 + 4j])


@sc.compute
def mean_iq(samples, gain) -> Annotated[
    complex, sc.ScalarType(sc.ComplexType(unit="V"))
]:
    return complex(np.mean(samples)) * gain


@sc.experiment(id="mean-iq")
def mean_iq_experiment(
    experiment: sc.ExperimentContext, *, gain: float = 1.0
) -> MeanData:
    samples = shot_iq()
    mean = mean_iq(samples, gain)
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
                sys.path.insert(0, str(project.root / "src"))
                imported = cast(
                    "Experiment[..., object]",
                    importlib.import_module(
                        "scopecat_lab.authored.signal"
                    ).mean_iq_experiment,
                )
                definition = session.load_experiment(imported)
                old_request = definition()
                run = session.prepare(old_request).run().wait(timeout=90).result()
                old_id = run.id
                variable = run.measurements()["iq"]
                assert variable.dtype == "complex128"
                assert variable.unit == "V"
                assert list(variable.require_values()) == [2 + 3j]
                source.write_text(
                    SOURCE.replace(
                        "return complex(np.mean(samples)) * gain",
                        "return complex(np.mean(samples)) * gain + 1j",
                    )
                    .replace("gain: float = 1.0", "gain: float = 2.0")
                    .replace("iq: sc.DataRef[complex]", "average: sc.DataRef[complex]"),
                    encoding="utf-8",
                )
                definition = session.refresh(definition)
                assert definition().snapshot()["gain"] == 2.0
                assert old_request.snapshot()["gain"] == 1.0
                assert (
                    session.prepare(old_request).preview.code_revision
                    == old_request.declaration.code_revision
                )
                changed = session.prepare(definition()).run().wait(timeout=90).result()
                new_id = changed.id
                assert list(changed.measurements()["average"].require_values()) == [
                    4 + 7j
                ]
                assert list(run.measurements()["iq"].require_values()) == [2 + 3j]
                admitted = session.state()
                good_source = source.read_text(encoding="utf-8")
                source.write_text(good_source + "\ndef broken(:\n", encoding="utf-8")
                try:
                    session.refresh(definition)
                except AuthorPreparationFailed:
                    pass
                else:
                    raise AssertionError("invalid source was accepted")
                assert session.state() == admitted
                assert definition().snapshot()["gain"] == 2.0
                source.write_text(good_source, encoding="utf-8")
                # Reconnect to a completed refresh; binding must not publish again.
                operation = session.begin_refresh()
                selected = operation.wait(timeout=90)
                rebound = session.load_experiment(
                    definition, code_revision=selected.active
                )
                assert rebound.code_revision == definition.code_revision
                assert session.state() == selected
        finally:
            stop_project(project)
        start_project(project, timeout=90)
        try:
            with project.authoring() as session:
                for run_id, field, expected in (
                    (old_id, "iq", 2 + 3j),
                    (new_id, "average", 4 + 7j),
                ):
                    variable = session.run(run_id).measurements()[field]
                    assert variable.unit == "V"
                    assert list(variable.require_values()) == [expected]
                # Inject the reported assembly failure inside an isolated run
                # worker: the notebook exception must carry its actual cause.
                source.write_text(
                    SOURCE.replace(
                        "return complex(np.mean(samples)) * gain",
                        "from scopecat.execution import interpreter\n"
                        "    def fail(*args, **kwargs):\n"
                        "        raise TypeError(\n"
                        "            'unsupported persisted scalar: complex')\n"
                        "    interpreter.project_measurement_records = fail\n"
                        "    return complex(np.mean(samples)) * gain",
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
