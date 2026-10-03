"""Capture and check author dependencies in their execution interpreter."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

from scopecat.project import open_project
from scopecat.project_sources import capture_sources, require_environment
from scopecat.records.author_revision import (
    AuthorRevisionBundle,
    AuthorRevisionManifest,
)


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
        raise ValueError(f"Author environment check timed out: {python}") from error
    if result.returncode:
        raise ValueError(
            f"Author execution environment {python}:\n{result.stderr[-8192:]}"
        )
    return result.stdout


def _capture(root: Path) -> AuthorRevisionBundle:
    return capture_sources(open_project(root))


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
        if sys.argv[1] == "capture":
            request = cast("dict[str, str | None]", json.loads(value))
            assert request["root"] is not None
            output = _capture(
                Path(request["root"]),
            ).model_dump_json()
        elif sys.argv[1] == "check":
            require_environment(AuthorRevisionManifest.model_validate_json(value))
            output = ""
        else:
            raise ValueError("Unknown author environment operation")
    print(output)


if __name__ == "__main__":
    main()
