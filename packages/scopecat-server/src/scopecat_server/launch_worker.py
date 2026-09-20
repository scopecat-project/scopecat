"""Execute project preview callbacks outside the instrument daemon process."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

import scopecat as sc
from pydantic import ValidationError
from scopecat.application.authoring import AuthorLaunchProvider
from scopecat.application.experiment_plans import validate_plan_launch
from scopecat.application.launch import (
    LaunchCatalog,
    LaunchPreview,
    LaunchRequestRejected,
    LaunchResult,
    validate_launch_control_edits,
)
from scopecat.author_workspaces import author_workspace_id
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.endpoint import DAEMON_URL_ENV, resolve_daemon_endpoint
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.kernel.frozen import thaw_json_value
from scopecat.kernel.interaction_timing import record_timing
from scopecat.project import load_project
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.launch_request import LaunchRequest

from scopecat_server.author_worker import revision_project
from scopecat_server.worker_diagnostics import (
    report_stage,
    report_validation_error,
)

if TYPE_CHECKING:
    from io import TextIOWrapper

    from scopecat.api.lab import LabClient
    from scopecat.application import LabApplication


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
    if len(sys.argv) == 4 and sys.argv[2] == "--serve":
        serve(root, AuthorRevisionRef(content_hash=sys.argv[3]))
        return
    request = LaunchRequest.model_validate_json(sys.stdin.read())
    project = load_project(root / "scopecat.toml")
    # Transport owns workspace qualification and immutable revision selection.
    # Only applications without an author baseline use this one-shot path.
    with contextlib.redirect_stdout(sys.stderr):
        report_stage("project application load")
        application = project.load_application()
        try:
            result = launch(application, root, None, request)
        except LaunchRequestRejected as error:
            encoded = error.diagnostic.model_dump_json()
        else:
            encoded = result.model_dump_json()
    print(encoded)


def run_project_procedure(root: Path, procedure_id: str) -> None:
    record_timing("procedure_identity_start", procedure_id=procedure_id)
    with DaemonClient(
        resolve_daemon_endpoint(root), workspace_id=author_workspace_id(root)
    ) as client:
        stored = client.get_procedure(procedure_id)
        plan_code = (
            client.experiment_plan(stored.plan_ref).definition.code_revision
            if stored.plan_ref is not None
            else None
        )
    identity = stored.intent.get("code_revision")
    if plan_code is not None:
        identity = plan_code.model_dump(mode="json")
    record_timing("source_restore_start", procedure_id=procedure_id)
    project = (
        revision_project(
            root, AuthorRevisionRef.model_validate(thaw_json_value(identity))
        )
        if identity is not None
        else sc.open_project(root)
    )
    record_timing("application_load_start", procedure_id=procedure_id)
    application = project.load_application()
    record_timing("application_ready", procedure_id=procedure_id)
    with application.connect(
        resolve_daemon_endpoint(root), operator="console-worker"
    ) as lab:
        run_procedure(lab, procedure_id)
    record_timing("procedure_return", procedure_id=procedure_id)


def launch(
    application: LabApplication,
    root: Path,
    ref: AuthorRevisionRef | None,
    request: LaunchRequest,
) -> LaunchResult:
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
            provider = application.launch_provider
            if isinstance(provider, AuthorLaunchProvider):
                provider = provider.resolve(lab)
            catalog = LaunchCatalog()
            if request.action != "list":
                catalog = provider(lab, LaunchRequest(action="list"))
                if not isinstance(catalog, LaunchCatalog):
                    raise TypeError("project list callback must return LaunchCatalog")
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
            result = provider(lab, request)
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
        result = result.model_copy(
            update={"code_revision": ref, "workspace_id": request.workspace_id}
        )
    return result


def serve(
    root: Path, ref: AuthorRevisionRef, *, application: LabApplication | None = None
) -> None:
    """One immutable import namespace, fresh connection and request per call."""
    for line in sys.stdin:
        started = time.perf_counter()
        request = LaunchRequest.model_validate_json(line)
        if request.code_revision != ref:
            raise ValueError("worker requires its exact author revision")
        phases: dict[str, float] = {}
        with contextlib.redirect_stdout(sys.stderr):
            if application is None:
                report_stage("author revision initialization")
                project = revision_project(root, ref)
                now = time.perf_counter()
                phases["revision"] = now - started
                report_stage("project application load")
                application = project.load_application()
                phases["application"] = time.perf_counter() - now
            provider_started = time.perf_counter()
            try:
                result = launch(application, root, ref, request)
            except LaunchRequestRejected as error:
                encoded = error.diagnostic.model_dump_json()
            else:
                encoded = result.model_dump_json()
            phases["provider"] = time.perf_counter() - provider_started
            phases["worker"] = time.perf_counter() - started
            print(
                "Scopecat launch timing: " + json.dumps(phases),
                file=sys.stderr,
                flush=True,
            )
        print(encoded, flush=True)


if __name__ == "__main__":
    # The subprocess JSON protocol and redirected diagnostics are UTF-8 on every OS.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        cast("TextIOWrapper", stream).reconfigure(encoding="utf-8")
    try:
        main()
    except ValidationError as error:
        # The parent reports the last diagnostic line. Preserve field locations
        # there instead of leaving only Pydantic's trailing documentation URL.
        report_validation_error(error)
        raise SystemExit(1) from None
