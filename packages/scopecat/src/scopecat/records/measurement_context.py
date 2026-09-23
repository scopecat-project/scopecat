"""Exact saved scientific inputs, independent of their use by calibration."""

from __future__ import annotations

from dataclasses import dataclass

from scopecat.records.content import Sha256ContentHash
from scopecat.records.execution_scenario import SoftwareExecutionScenario
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    ResolvedSubject,
    TargetSetupBinding,
)


@dataclass(frozen=True)
class MeasurementContext:
    """Frozen inputs using an exact saved parameter revision without overrides.

    Mutable branch choices and resolution receipts are separate. This is not the
    complete execution provenance, nor proof of calibration applicability.
    """

    parameters: ParameterRevisionRef
    subject: ResolvedSubject
    setup_content_hash: Sha256ContentHash
    scenario: SoftwareExecutionScenario | None
    target_binding: TargetSetupBinding | None = None

    @classmethod
    def from_binding(
        cls, parameters: ParameterRevisionRef, binding: ResolvedScientificBinding
    ) -> MeasurementContext:
        """Capture all scientific binding fields without reading mutable heads."""
        return cls(
            parameters=parameters,
            subject=binding.subject,
            setup_content_hash=binding.setup_content_hash,
            scenario=binding.scenario,
            target_binding=binding.target_binding,
        )
