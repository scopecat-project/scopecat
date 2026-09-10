"""Shared hardware-free inputs for the everyday-author acceptance journey.

This is maintainer fixture assembly using today's APIs, not the proposed novice
facade. Runs use the existing exploratory experiment and production recording.
"""

from dataclasses import dataclass

import scopecat as sc
from scopecat.api.lab import LabClient
from scopecat.records.config import ConfigProfileSnapshot

from reference_lab.exploration import exploration_config
from reference_lab.workflows.exploratory_signal import exploratory_signal


@dataclass(frozen=True, slots=True)
class EverydayAuthorInputs:
    """Known and unknown snapshots; neither is an accepted measured calibration."""

    known: ConfigProfileSnapshot
    missing: ConfigProfileSnapshot


def everyday_author_inputs() -> EverydayAuthorInputs:
    """Reuse the existing reference table; the missing carrier blocks its consumer.

    Existing records encode unknown by an absent cell. A full user-facing table
    with explicit None cells is a later implementation, not supplied here.
    """
    return EverydayAuthorInputs(
        known=exploration_config(sc.Quantity(4.8, "GHz")),
        missing=exploration_config(None),
    )


@dataclass(frozen=True, slots=True)
class EverydayAuthorRuns:
    """Retained input identities, with no fabricated fit or measurement receipt."""

    peaked: str
    flat: str


def acquire_everyday_author_inputs(lab: LabClient) -> EverydayAuthorRuns:
    """Record a peaked and a flat response in a caller-owned reference project.

    The second acquisition succeeds but cannot identify a resonance. This keeps
    acquisition status distinct from scientific usefulness. No default is set.
    """
    config = everyday_author_inputs().known
    return EverydayAuthorRuns(
        peaked=lab.run(
            exploratory_signal(gain=1.0), config=config, name="Author fixture: peak"
        ).id,
        flat=lab.run(
            exploratory_signal(gain=0.0), config=config, name="Author fixture: flat"
        ).id,
    )
