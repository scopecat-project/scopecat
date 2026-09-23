"""Policy graph selection preserves scientific prerequisites without query limits."""

from datetime import timedelta

import pytest

from scopecat.records.calibration_check import CalibrationScope
from scopecat.records.calibration_policy import (
    CalibrationProfile,
    CalibrationRequirement,
)


def test_profile_selection_closes_diamond_and_preserves_policy_order() -> None:
    dependencies = {
        "joint": ("a", "b"),
        "a": ("base",),
        "b": ("base",),
        "base": (),
        "other": (),
    }
    profile = CalibrationProfile(
        id="daily",
        requirements=tuple(
            CalibrationRequirement(
                id=identity,
                scope=CalibrationScope("readout", (identity,), "cold", "1"),
                max_age=timedelta(hours=1),
                depends_on=parents,
            )
            for identity, parents in dependencies.items()
        ),
    )
    selected = profile.select(("joint", "base"))
    assert tuple(item.id for item in selected) == ("joint", "a", "b", "base")
    assert selected[0].depends_on == ("a", "b")
    assert profile.requirements[-1].id == "other"
    for invalid in ((), ("a", "a"), ("missing",)):
        with pytest.raises(ValueError, match="requirement"):
            profile.select(invalid)
