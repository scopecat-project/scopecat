"""Execute project preview callbacks outside the instrument daemon process."""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import httpx2
import scopecat as sc
from scopecat.application.experiment_plans import validate_plan_launch
from scopecat.application.launch import (
    LaunchCatalog,
    LaunchPreview,
    LaunchResult,
    validate_launch_control_edits,
)
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.endpoint import DAEMON_URL_ENV, resolve_daemon_endpoint
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.kernel.frozen import thaw_json_value
from scopecat.project import load_project
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.launch_request import LaunchRequest

from scopecat_server.author_worker import revision_project
from scopecat_server.worker_diagnostics import (
    AUTHOR_VALIDATION_TIMEOUT_EXIT,
    report_stage,
)

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
            plan_code = (
                client.experiment_plan(stored.plan_ref).definition.code_revision
                if stored.plan_ref is not None
                else None
            )
        identity = stored.intent.get("code_revision")
        if plan_code is not None:
            identity = plan_code.model_dump(mode="json")
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
        report_stage("author revision initialization")
        with DaemonClient(resolve_daemon_endpoint(root)) as client:
            try:
                state = client.author_revision_state()
            except httpx2.HTTPStatusError as error:
                if error.response.status_code != 504:
                    raise
                payload = cast("object", error.response.json())
                if not isinstance(payload, dict):
                    raise
                detail = cast("dict[str, object]", payload).get("detail")
                if not isinstance(detail, str):
                    raise
                # Preserve this known validation failure across the existing
                # process boundary, rather than the HTTP exception's last line.
                print(" ".join(detail.splitlines())[:2048], file=sys.stderr)
                raise SystemExit(AUTHOR_VALIDATION_TIMEOUT_EXIT) from None
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
        report_stage("project application load")
        application = project.load_application()
        result: LaunchResult
        if application.launch_provider is None:
            result = LaunchCatalog()
            if request.action != "list":
                raise ValueError("project has no experiment preview provider")
        else:
            report_stage("launch provider")
            with application.connect(
                resolve_daemon_endpoint(root), operator=request.actor
            ) as lab:
                catalog = LaunchCatalog()
                if request.action != "list":
                    catalog = application.launch_provider(
                        lab, LaunchRequest(action="list")
                    )
                    if not isinstance(catalog, LaunchCatalog):
                        raise TypeError(
                            "project list callback must return LaunchCatalog"
                        )
                    validate_launch_control_edits(catalog, request)
                    if request.plan_ref is not None:
                        plan = lab.plans.get(request.plan_ref)
                        entry = next(
                            (
                                entry
                                for entry in catalog.entries
                                if entry.id == request.experiment
                                and entry.version == request.version
                            ),
                            None,
                        )
                        if entry is None:
                            raise ValueError(
                                "saved plan experiment is "
                                "unavailable in this author revision"
                            )
                        validate_plan_launch(plan, request, entry)
                result = application.launch_provider(lab, request)
                if isinstance(result, LaunchPreview):
                    entry = next(
                        (
                            item
                            for item in catalog.entries
                            if item.id == request.experiment
                            and item.version == request.version
                        ),
                        None,
                    )
                    if entry is not None:
                        result = result.model_copy(
                            update={
                                "plan_ref": request.plan_ref,
                                "definition_hash": sha256_json_hash(
                                    entry.model_dump(mode="json")
                                ),
                            }
                        )
    if isinstance(result, (LaunchCatalog, LaunchPreview)):
        result = result.model_copy(update={"code_revision": ref})
    print(result.model_dump_json())


if __name__ == "__main__":
    # The subprocess JSON protocol and redirected diagnostics are UTF-8 on every OS.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        cast("TextIOWrapper", stream).reconfigure(encoding="utf-8")
    main()
