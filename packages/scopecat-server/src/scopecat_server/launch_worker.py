"""Execute project preview callbacks outside the instrument daemon process."""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import scopecat as sc
from scopecat.application.launch import (
    LaunchCatalog,
    LaunchPreview,
    LaunchRequest,
    LaunchResult,
    validate_launch_control_edits,
)
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.endpoint import DAEMON_URL_ENV, resolve_daemon_endpoint
from scopecat.kernel.frozen import thaw_json_value
from scopecat.project import load_project
from scopecat.records.author_revision import AuthorRevisionRef

from scopecat_server.author_worker import revision_project

if TYPE_CHECKING:
    from io import TextIOWrapper

    from scopecat.api.lab import LabClient


def run_procedure(lab: LabClient, procedure_id: str) -> None:
    """Run until the next durable boundary; review waits belong to the manager."""
    handle = lab.procedures.get(procedure_id)
    if handle.state == "ready":
        handle.resume()


def main() -> None:
    # This internal worker belongs to its spawning project, even when the server
    # inherited a user's endpoint override for a different interactive session.
    os.environ.pop(DAEMON_URL_ENV, None)
    root = Path(sys.argv[1]).resolve()
    if len(sys.argv) == 4 and sys.argv[2] == "--procedure":
        with DaemonClient(resolve_daemon_endpoint(root)) as client:
            stored = client.get_procedure(sys.argv[3])
        identity = stored.intent.get("code_revision")
        project = (
            revision_project(
                root, AuthorRevisionRef.model_validate(thaw_json_value(identity))
            )
            if identity is not None
            else sc.open_project(root)
        )
        with project.connect(operator="console-worker") as lab:
            run_procedure(lab, sys.argv[3])
        return
    request = LaunchRequest.model_validate_json(sys.stdin.read())
    project = load_project(root / "scopecat.toml")
    ref = request.code_revision
    if project.source_roots or ref is not None:
        with DaemonClient(resolve_daemon_endpoint(root)) as client:
            state = client.author_revision_state()
        ref = ref or state.active
        if (
            state.enabled
            and request.action == "submit"
            and request.code_revision is None
        ):
            raise ValueError("submit requires the preview's author code revision")
        if ref is not None:
            project = revision_project(root, ref)
    with contextlib.redirect_stdout(sys.stderr):
        application = project.load_application()
        result: LaunchResult
        if application.launch_provider is None:
            result = LaunchCatalog()
            if request.action != "list":
                raise ValueError("project has no experiment preview provider")
        else:
            with application.connect(
                resolve_daemon_endpoint(root), operator=request.actor
            ) as lab:
                if request.control_edits:
                    catalog = application.launch_provider(
                        lab, LaunchRequest(action="list")
                    )
                    if not isinstance(catalog, LaunchCatalog):
                        raise TypeError(
                            "project list callback must return LaunchCatalog"
                        )
                    validate_launch_control_edits(catalog, request)
                result = application.launch_provider(lab, request)
    if isinstance(result, (LaunchCatalog, LaunchPreview)):
        result = result.model_copy(update={"code_revision": ref})
    print(result.model_dump_json())


if __name__ == "__main__":
    # The subprocess JSON protocol and redirected diagnostics are UTF-8 on every OS.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        cast("TextIOWrapper", stream).reconfigure(encoding="utf-8")
    main()
