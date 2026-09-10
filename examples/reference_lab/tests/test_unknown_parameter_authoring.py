"""New table declaration and unknown consumption through real daemon admission."""

import os

import pytest
import scopecat as sc
from scopecat.kernel.errors import CheckFailed
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.sample import SampleRevisionDraft

from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.workflows.unknown_parameters import (
    ProbeParameters,
    duration_probe,
    pi_probe,
)


def test_new_table_unknowns_freeze_and_structural_history() -> None:
    application = create_application(EXAMPLE_ROOT)
    with application.connect(os.environ["SCOPECAT_DAEMON_URL"]) as lab:
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
        probes = params.declare_table("probes", ProbeParameters, key="id")
        probes.add(ProbeParameters("q0", 40))
        probes.add(ProbeParameters("q1", 60))
        initial = params.save("author-unknown-probes")
        assert params["probes"]["q0"]["pi_amplitude"] is None
        # Imports require the consumed column; the other unknown never blocks.
        run = lab.run(duration_probe(), config=params.freeze())
        assert run.status == "completed"
        original = run.snapshot
        with pytest.raises(CheckFailed, match=r"probes.*q0.*pi_amplitude.*unknown"):
            lab.preview(pi_probe(), config=params.freeze())
        probes["q0"].pi_amplitude = 0.2
        frozen = params.freeze()
        prepared = lab.prepare(pi_probe(), config=frozen)
        assert prepared.preview().point_count == 1
        probes["q0"].pi_amplitude = None
        filled = prepared.run()
        assert filled.status == "completed"
        assert filled.config == frozen.config
        assert lab.get_run(run.id).snapshot == original
        # Structural changes make a new immutable context, never rewrite the old run.
        params.discard()
        params.convert_unit("probes", "duration", "us")
        params.rename_column("probes", "duration", "pulse_length")
        params.save("author-unknown-renamed")
        assert params["probes"]["q0"]["pulse_length"] == sc.Quantity(0.04, "us")
        old = lab.config.workspace(context=initial)
        assert old["probes"]["q0"]["duration"] == sc.Quantity(40, "ns")
        assert lab.get_run(run.id).snapshot == original
        assert lab.config.active() == active
