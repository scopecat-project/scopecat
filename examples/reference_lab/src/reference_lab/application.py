"""Daemon bootstrap and project-worker composition for the reference lab."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from scopecat.application import LabBootstrap

from reference_lab.configuration import bootstrap_config

if TYPE_CHECKING:
    from scopecat.application import LabApplication


def create_bootstrap(project_root: Path) -> LabBootstrap:
    """Expose only config construction to the daemon process."""

    config_dir = project_root / "config"
    return LabBootstrap(
        bootstrap_config=lambda: bootstrap_config(config_dir),
    )


def create_application(_project_root: Path) -> LabApplication:
    """Compose notebook and project-worker execution capabilities."""

    from scopecat.application import LabApplication

    from reference_lab.lab import reference_lab_system
    from reference_lab.workflows.drag_beta_automatic_publication import (
        DRAG_BETA_PUBLICATION_POLICY_REGISTRY,
    )
    from reference_lab.workflows.drag_beta_freshness import (
        DRAG_BETA_CALIBRATION_REGISTRY,
    )
    from reference_lab.workflows.drag_beta_procedure import (
        drag_beta_calibration_procedure,
        drag_beta_verification_procedure,
    )
    from reference_lab.workflows.temperature_diagnostic import (
        temperature_diagnostic_procedure,
    )

    return LabApplication(
        build_experiment_system=lambda config, instrument_catalog: reference_lab_system(
            config=config,
            instrument_catalog=instrument_catalog,
        ),
        procedures=(
            temperature_diagnostic_procedure,
            drag_beta_calibration_procedure,
            drag_beta_verification_procedure,
        ),
        calibrations=DRAG_BETA_CALIBRATION_REGISTRY,
        calibration_publications=DRAG_BETA_PUBLICATION_POLICY_REGISTRY,
    )


__all__ = ["create_application", "create_bootstrap"]
