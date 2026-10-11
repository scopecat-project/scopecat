"""Paths and version-controlled bootstrap config for the reference lab."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from scopecat.config.resolution import compose_configuration
from scopecat.records.config import (
    ConfigProfileSnapshot,
)
from scopecat.records.parameter_revision import ParameterRevisionContent
from scopecat.records.setup import ExecutableSetupSnapshot

EXAMPLE_ROOT = Path(__file__).resolve().parents[2]
DEMO_CONFIG_DIR = EXAMPLE_ROOT / "config"
DAEMON_URL_ENV = "SCOPECAT_DAEMON_URL"


def initial_setup(
    config_dir: str | Path = DEMO_CONFIG_DIR,
) -> ExecutableSetupSnapshot:
    """Load equipment without importing parameter tables into its declaration."""

    root = Path(config_dir)
    document = cast(
        "dict[str, object]",
        json.loads((root / "system-infrastructure.json").read_text(encoding="utf-8")),
    )
    document.pop("id")
    return ExecutableSetupSnapshot.model_validate(document)


def initial_parameters() -> ParameterRevisionContent:
    from reference_lab.parameters import (
        REFERENCE_PARAMETER_CATALOG,
        reference_lab_parameter_snapshot,
    )

    return ParameterRevisionContent(
        id="reference-lab-profile",
        system_id="reference-lab-system",
        catalog=REFERENCE_PARAMETER_CATALOG,
        parameters=reference_lab_parameter_snapshot(),
    )


def bootstrap_config(config_dir: str | Path = DEMO_CONFIG_DIR) -> ConfigProfileSnapshot:
    """Compose the execution carrier used by retained reference workflows."""
    parameters = initial_parameters()
    return compose_configuration(
        initial_setup(config_dir),
        id=parameters.id,
        system_id=parameters.system_id,
        catalog=parameters.catalog,
        parameters=parameters.parameters,
    )


__all__ = [
    "DAEMON_URL_ENV",
    "DEMO_CONFIG_DIR",
    "EXAMPLE_ROOT",
    "bootstrap_config",
    "initial_parameters",
    "initial_setup",
]
