"""Capture bundled drivers as ordinary immutable local source, also in dev builds."""

import shutil
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from scopecat_server.author_environment import DriverSourceCapture, capture_driver


def capture_builtin(
    name: Literal["public", "simulation"], python: Path
) -> DriverSourceCapture:
    spec = find_spec("scopecat_instruments")
    if spec is None or spec.origin is None:
        raise ValueError(
            "Built-in drivers require scopecat-instruments in the application "
            "environment. Install the standard scopecat-app distribution."
        )
    template = Path(spec.origin).parent / "builtin_drivers" / name
    with TemporaryDirectory(prefix="scopecat-builtin-drivers-") as directory:
        source = Path(directory) / name
        shutil.copytree(template, source, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(
            Path(spec.origin).parent,
            source / "src" / "scopecat_instruments",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        return capture_driver(source, python)
