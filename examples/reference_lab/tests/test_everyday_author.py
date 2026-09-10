"""The shared author inputs pass through real admission, storage and analysis."""

import os

import numpy as np
import pytest

from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.everyday_author import (
    acquire_everyday_author_inputs,
    everyday_author_inputs,
)
from reference_lab.workflows.exploratory_signal import (
    exploratory_mean,
    exploratory_signal,
)


def test_everyday_author_retained_inputs_and_unknown_consumer() -> None:
    application = create_application(EXAMPLE_ROOT)
    endpoint = os.environ["SCOPECAT_DAEMON_URL"]
    with application.connect(endpoint) as lab:
        active = lab.config.active()
        inputs = everyday_author_inputs()
        with pytest.raises((ValueError, KeyError), match="drive_carrier_frequency"):
            lab.preview(exploratory_signal(), config=inputs.missing)
        assert (
            lab.prepare(exploratory_signal(), config=inputs.known).preview().point_count
            == 5
        )
        acquired = acquire_everyday_author_inputs(lab)
        run = lab.get_run(acquired.peaked)
        flat = lab.get_run(acquired.flat)
        assert run.status == flat.status == "completed"
        original = run.snapshot
        values = np.asarray(run.measurements()["result"].require_values()).copy()
        assert np.argmax(values) == 1
        np.testing.assert_array_equal(
            flat.measurements()["result"].require_values(), np.zeros(5)
        )
        run_ids = {item.id for item in lab.runs().items}
        receipt = run.analyze(exploratory_mean(minimum=0.5))
        assert receipt.fact("selected-points").value == 1
        assert receipt.fact("mean-response").value == 1.0
        assert receipt.view.analysis.inputs
        with pytest.raises(ValueError, match="No retained values"):
            flat.analyze(exploratory_mean(minimum=0.5))
        assert {item.id for item in lab.runs().items} == run_ids
        assert lab.config.active() == active
    with application.connect(endpoint) as lab:
        reopened = lab.get_run(acquired.peaked)
        assert reopened.snapshot == original
        np.testing.assert_array_equal(
            reopened.measurements()["result"].require_values(), values
        )
        assert lab.config.active() == active
