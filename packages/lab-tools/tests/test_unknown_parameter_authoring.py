"""Unknown parameter admission and history need no device/quantum integration."""

from pathlib import Path

import pytest

import scopecat as sc
from lab_teaching.project import create_project
from scopecat.kernel.errors import CheckFailed
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.sample import SampleRevisionDraft
from scopecat_server.lifecycle import start_project, stop_project


class ProbeParameters(sc.ParameterModel, table="probes"):
    id: sc.Param[str] = sc.param(key=True)
    duration: sc.Magnitude[float] = sc.quantity(unit="ns")
    pi_amplitude: sc.Param[float | None] = sc.param(default=None)


@sc.experiment(id="tutorial.duration_probe")
def duration_probe(experiment: sc.ExperimentContext) -> sc.ValueRef[sc.Quantity]:
    del experiment
    return sc.parameter_ref(ProbeParameters.duration, "q0")


@sc.experiment(id="tutorial.pi_probe")
def pi_probe(experiment: sc.ExperimentContext) -> sc.ValueRef[float]:
    del experiment
    return sc.parameter_ref(ProbeParameters.pi_amplitude, "q0")


def test_new_table_unknowns_freeze_and_structural_history(
    tmp_path: Path, notebook_imports
) -> None:
    root = create_project(tmp_path / "unknown-parameters").parent
    project = sc.open_project(root)
    start_project(project)
    try:
        with project.connect() as lab:
            active = lab.config.active()
            lab.samples.create(
                "author-unknown",
                kind="synthetic",
                content=SampleRevisionDraft(display_name="Unknown author sample"),
            )
            lab.config.save_context(
                entry_id="author-unknown-start",
                base=ConfigContextRef(
                    entry_id=active.entry.id, content_hash=active.entry.content_hash
                ),
                sample=lab.samples.handle("author-unknown").selector(),
                working_point_id="new",
                label="New sample",
            )
            params = lab.config.workspace(context="author-unknown-start")
            probes = params.declare_table(ProbeParameters)
            probes.add(ProbeParameters(id="q0", duration=40))
            probes.add(ProbeParameters(id="q1", duration=60))
            initial = params.save("author-unknown-probes")
            assert params[ProbeParameters]["q0"].duration == 40
            assert (
                lab.config.workspace(context=initial)[ProbeParameters]["q0"].duration
                == 40
            )
            assert params["probes"]["q0"]["pi_amplitude"] is None
            # Imports require the consumed column; the other unknown never blocks.
            run = lab.run(duration_probe.build(), config=params.freeze())
            assert run.status == "completed"
            original = run.snapshot
            with pytest.raises(CheckFailed, match=r"probes.*q0.*pi_amplitude.*unknown"):
                lab.preview(pi_probe.build(), config=params.freeze())
            probes["q0"].pi_amplitude = 0.2
            frozen = params.freeze()
            prepared = lab.prepare(pi_probe.build(), config=frozen)
            assert prepared.preview().point_count == 1
            probes["q0"].pi_amplitude = None
            filled = prepared.run()
            assert filled.status == "completed"
            assert filled.config == frozen.config
            assert lab.get_run(run.id).snapshot == original
            # Structural changes make a new context; the old run stays immutable.
            params.discard()
            params.convert_unit("probes", "duration", "us")
            params.rename_column("probes", "duration", "pulse_length")
            params.save("author-unknown-renamed")
            assert params["probes"]["q0"]["pulse_length"] == sc.Quantity(0.04, "us")
            old = lab.config.workspace(context=initial)
            assert old["probes"]["q0"]["duration"] == sc.Quantity(40, "ns")
            assert lab.get_run(run.id).snapshot == original
            assert lab.config.active() == active
    finally:
        stop_project(project)
