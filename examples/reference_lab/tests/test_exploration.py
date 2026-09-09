"""Observable exploration baseline on retained production HTTP/store records."""

from __future__ import annotations

import os

import numpy as np
import pytest
from scopecat.records.config import config_content_hash
from scopecat.records.sample import SampleRevisionDraft

from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.exploration import (
    exploration_cases,
    exploration_config,
    seed_exploration,
)
from reference_lab.workflows.exploratory_signal import (
    exploratory_mean,
    exploratory_signal,
)


def test_exploration_retains_distinct_contexts_and_reanalyzes() -> None:
    with create_application(EXAMPLE_ROOT).connect(
        os.environ["SCOPECAT_DAEMON_URL"]
    ) as lab:
        active = lab.config.active()
        run_ids = seed_exploration(lab)
        cases = exploration_cases()
        assert len(set(run_ids)) == 4
        assert len({config_content_hash(case.config) for case in cases}) == 4
        original = lab.get_run(run_ids[0])
        snapshot, request = original.snapshot, original.request
        values = np.asarray(original.measurements()["result"].require_values()).copy()
        for run_id, case, peak in zip(run_ids, cases, (1, 2, 3, 4), strict=True):
            run = lab.get_run(run_id)
            assert run.status == "completed"
            assert run.samples[0].sample_id == case.sample_id
            assert run.samples[0].context_id == case.context_id
            assert run.snapshot.config_content_hash == config_content_hash(case.config)
            assert (
                np.argmax(
                    np.asarray(
                        run.measurements()["result"].require_values(), dtype=float
                    )
                )
                == peak
            )
        admitted = {run.id for run in lab.runs().items}
        first = original.analyze(exploratory_mean(minimum=0))
        second = original.analyze(exploratory_mean(minimum=0.5))
        assert {run.id for run in lab.runs().items} == admitted
        assert first.view.entry.id != second.view.entry.id
        assert first.view.analysis.inputs == second.view.analysis.inputs
        assert first.view.analysis.outputs != second.view.analysis.outputs
        chip = lab.samples.handle(cases[0].sample_id)
        chip.revise(
            SampleRevisionDraft(
                display_name="Synthetic A revised",
                topology=chip.revision(1).content.topology,
            ),
            expected_revision=1,
            note="Prove retained sample bindings survive revision",
        )
        assert original.snapshot == snapshot
        assert original.request == request
        assert lab.config.active() == active
        np.testing.assert_array_equal(
            original.measurements()["result"].require_values(), values
        )
    with create_application(EXAMPLE_ROOT).connect(
        os.environ["SCOPECAT_DAEMON_URL"]
    ) as lab:
        retained = lab.get_run(run_ids[0])
        assert retained.snapshot == snapshot
        assert retained.request == request
        np.testing.assert_array_equal(
            retained.measurements()["result"].require_values(), values
        )


def test_missing_carrier_is_not_replaced_with_a_known_working_value() -> None:
    with create_application(EXAMPLE_ROOT).connect(
        os.environ["SCOPECAT_DAEMON_URL"]
    ) as lab:
        active = lab.config.active()
        with pytest.raises((ValueError, KeyError), match="drive_carrier_frequency"):
            lab.preview(exploratory_signal(), config=exploration_config(None))
        assert lab.config.active() == active
