"""Capture production reference-lab responses in a temporary, hardware-free project."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from reference_lab.acceptance import acceptance_json, capture_acceptance_fixtures
from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT
from scopecat.daemon.client import DaemonClient
from scopecat.project import load_project
from scopecat_server.lifecycle import start_project, stop_project

OUTPUT = EXAMPLE_ROOT / "fixtures" / "acceptance.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="scopecat-acceptance-") as temporary:
        root = Path(temporary)
        shutil.copytree(EXAMPLE_ROOT / "config", root / "config")
        shutil.copytree(EXAMPLE_ROOT / "src", root / "src")
        shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", root / "scopecat.toml")
        project = load_project(root / "scopecat.toml")
        endpoint = start_project(project)
        try:
            with (
                create_application(root).connect(endpoint.base_url) as lab,
                DaemonClient(endpoint.base_url) as client,
            ):
                content = acceptance_json(capture_acceptance_fixtures(lab, client))
        finally:
            stop_project(project)
    if cast("bool", args.check):
        if not OUTPUT.is_file() or OUTPUT.read_text() != content:
            raise SystemExit(
                "Reference-lab acceptance fixture is stale; run "
                "uv run python scripts/generate_reference_lab_acceptance.py"
            )
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(content)


if __name__ == "__main__":
    main()
