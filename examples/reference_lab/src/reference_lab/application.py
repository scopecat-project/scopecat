"""Daemon bootstrap and project-worker composition for the reference lab."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from scopecat.application import LabBootstrap

from reference_lab.configuration import initial_parameters, initial_setup

if TYPE_CHECKING:
    from scopecat.application import LabApplication


def create_bootstrap(project_root: Path) -> LabBootstrap:
    """Expose only config construction to the daemon process."""

    config_dir = project_root / "config"
    return LabBootstrap(
        setup=lambda: initial_setup(config_dir),
        parameter_defaults=initial_parameters,
    )


def create_application(_project_root: Path) -> LabApplication:
    """Compose notebook and project-worker execution capabilities."""

    from scopecat.application import LabApplication

    from reference_lab.comparison import comparison_provider
    from reference_lab.lab import reference_lab_system
    from reference_lab.launch import (
        launch_channel_timing,
        launch_provider,
    )
    from reference_lab.workflows.analysis_recovery import (
        failed_temperature_analysis,
        recovered_temperature_analysis,
    )
    from reference_lab.workflows.drag_branch_calibration import drag_branch_calibration
    from reference_lab.workflows.temperature_diagnostic import (
        temperature_diagnostic_procedure,
    )

    return LabApplication(
        launch_provider=launch_provider,
        comparison_provider=comparison_provider,
        author_modules=(
            "reference_lab.workflows.authored",
            "reference_lab.workflows.frequency_amplitude",
            "reference_lab.workflows.temperature_diagnostic",
        ),
        build_experiment_system=lambda config, instrument_catalog: reference_lab_system(
            config=config,
            instrument_catalog=instrument_catalog,
        ),
        procedures=(
            launch_channel_timing,
            temperature_diagnostic_procedure,
            failed_temperature_analysis,
            recovered_temperature_analysis,
            drag_branch_calibration,
        ),
    )


__all__ = ["create_application", "create_bootstrap"]
