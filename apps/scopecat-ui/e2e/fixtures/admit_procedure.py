"""Admit a retained browser procedure without dispatching it."""

import sys

import scopecat as sc
from scopecat.application.launch import LaunchPreview, LaunchSubmission
from scopecat.author_workspaces import author_workspace_id
from scopecat.daemon.endpoint import resolve_daemon_endpoint
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.scientific_selection import (
    ParameterConfiguration,
    ScientificSelection,
)
from scopecat_server.author_worker import (  # noqa: TID251 - admission-only integration fixture
    revision_project,
)

project = sc.open_project(sys.argv[1])
with project.authoring() as author:
    catalog = author.catalog()
assert catalog.code_revision is not None
# Seed the admission-only boundary with the same immutable composition that the
# worker restores. The public author submit API also dispatches the procedure.
application = revision_project(project.root, catalog.code_revision).load_application()
with application.connect(
    resolve_daemon_endpoint(project.root), workspace_id=catalog.workspace_id
) as lab:
    provider = application.launch_provider
    assert provider is not None
    assert application.authors is not None
    entry = application.authors.get("reference_lab.temperature_diagnostic").entry
    catalog_entry = next(item for item in catalog.entries if item.id == entry.id)
    assert entry == catalog_entry
    parameters = lab.parameters.checkout("browser").head.revision
    setup = lab.setup.get("browser-bench-a")
    request = LaunchRequest(
        workspace_id=author_workspace_id(project.root),
        action="preview",
        experiment=entry.id,
        version=entry.version,
        selection=ScientificSelection(
            configuration=ParameterConfiguration(ref=parameters, setup=setup.ref)
        ),
    )
    preview = provider(lab, request)
    assert isinstance(preview, LaunchPreview)
    admitted = provider(
        lab,
        LaunchRequest.model_validate(
            {
                **request.model_dump(),
                "action": "submit",
                "request_key": "browser-retained",
                "expected_request_hash": preview.request_hash,
                "reviewed": preview.reviewed,
                "manual_state": preview.manual_state,
            }
        ),
    )
    assert isinstance(admitted, LaunchSubmission)
    retained = lab.procedures.get(admitted.procedure_id).snapshot
    assert retained.source is not None
    assert retained.source.workspace_id == catalog.workspace_id
    assert retained.source.code_revision == catalog.code_revision
    assert retained.definition.id == f"scopecat.author:{entry.id}"
    assert retained.definition.fingerprint == catalog_entry.version
    print(admitted.procedure_id)
