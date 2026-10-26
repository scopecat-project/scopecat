"""Prepare the existing Windows app or a Linux-only homologous teaching fixture.

This is acceptance plumbing, not a Linux native application builder. The format 1
input remains available for separate installer regression; the journey consumes
only the installed Python and verified format 2 resources.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.app_build.author_resources import build as build_author_resources
from scripts.app_build.runtime_install import install_runtime

from scopecat_app.bundle import verify_bundle


def build(payload: Path, destination: Path) -> None:
    if sys.platform == "win32":
        from scripts.app_build.native_package import build as build_native

        build_native(payload, destination)
        return
    if sys.platform != "linux":
        raise ValueError("Teaching acceptance supports Windows app and Linux fixture")
    verify_bundle(payload)
    destination.mkdir(parents=True, exist_ok=False)
    resources = destination / "resources"
    python_home = resources / "python"
    python = install_runtime(payload, python_home)
    build_author_resources(payload, resources / "payload", python=python)
    (destination / "linux-fixture.txt").write_text(
        "Installed runtime fixture only; no Linux native application qualification.\n",
        encoding="utf-8",
    )


class Arguments(argparse.Namespace):
    payload: Path = Path()
    destination: Path = Path()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args(namespace=Arguments())
    build(args.payload.resolve(), args.destination.resolve())
