"""Acceptance startup fixture: equipment authority without parameter defaults."""

from pathlib import Path

from scopecat.application import LabBootstrap

from reference_lab.configuration import initial_setup


def create_bootstrap(root: Path) -> LabBootstrap:
    return LabBootstrap(setup=lambda: initial_setup(root / "config"))
