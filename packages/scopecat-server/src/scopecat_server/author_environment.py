"""Capture and check author dependencies in their execution interpreter."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

from pydantic import BaseModel
from scopecat.project import open_project
from scopecat.project_sources import capture_sources, require_environment
from scopecat.records.author_revision import (
    AuthorRevisionBundle,
    AuthorRevisionManifest,
)

from scopecat_server.worker_diagnostics import diagnostic_excerpt


def _call(python: Path, action: str, value: str) -> str:
    environment = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "SCOPECAT_DAEMON_URL"):
        environment.pop(name, None)
    try:
        result = subprocess.run(  # noqa: S603 - trusted interpreter and fixed module
            [str(python), "-m", "scopecat_server.author_environment", action],
            input=value,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            timeout=60,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as error:
        _, evidence = diagnostic_excerpt(error.stderr)
        detail = (
            f"Author environment {action} timed out after {error.timeout:g} seconds: "
            f"{python}"
        )
        if evidence:
            detail += "\n" + evidence
        raise ValueError(detail) from error
    if result.returncode:
        raise ValueError(
            f"Author execution environment {python}:\n{result.stderr[-8192:]}"
        )
    return result.stdout


def _capture(root: Path) -> AuthorRevisionBundle:
    return capture_sources(open_project(root))


class DriverSourceCapture(BaseModel):
    bundle: AuthorRevisionBundle
    factory: str


def _capture_driver(root: Path) -> DriverSourceCapture:
    project = open_project(root)
    if not project.source_roots or project.instrument_backend_spec is None:
        raise ValueError(
            "source project must declare source roots and a driver factory"
        )
    return DriverSourceCapture(
        bundle=capture_sources(project), factory=project.instrument_backend_spec
    )


def capture_driver(root: Path, python: Path) -> DriverSourceCapture:
    """Resolve installed driver declarations in their owning environment."""
    if python == Path(sys.executable).absolute():
        return _capture_driver(root)
    return DriverSourceCapture.model_validate_json(
        _call(python, "capture-driver", json.dumps({"root": str(root)}))
    )


def capture(root: Path, python: Path) -> AuthorRevisionBundle:
    if python == Path(sys.executable).absolute():
        return _capture(root)
    return AuthorRevisionBundle.model_validate_json(
        _call(
            python,
            "capture",
            json.dumps({"root": str(root)}),
        )
    )


def check(manifest: AuthorRevisionManifest, python: Path) -> None:
    if python == Path(sys.executable).absolute():
        require_environment(manifest)
    else:
        _call(python, "check", manifest.model_dump_json())


def main() -> None:
    value = sys.stdin.read()
    with contextlib.redirect_stdout(sys.stderr):
        if sys.argv[1] in ("capture", "capture-driver"):
            request = cast("dict[str, str | None]", json.loads(value))
            assert request["root"] is not None
            root = Path(request["root"])
            captured = (
                _capture(root) if sys.argv[1] == "capture" else _capture_driver(root)
            )
            output = captured.model_dump_json()
        elif sys.argv[1] == "check":
            require_environment(AuthorRevisionManifest.model_validate_json(value))
            output = ""
        else:
            raise ValueError("Unknown author environment operation")
    print(output)


if __name__ == "__main__":
    main()
