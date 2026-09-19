"""Request-scoped catalog composition at the worker's admission boundary."""

from contextlib import nullcontext
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
import scopecat as sc
from scopecat.api.lab import LabClient
from scopecat.application import LabApplication
from scopecat.application.authoring import AuthorExperiment, AuthorExperiments
from scopecat.application.launch import (
    LaunchCatalog,
    LaunchCatalogEntry,
    LaunchInputSchema,
    LaunchPreview,
    LaunchRequestRejected,
)
from scopecat.records.control_edit import ControlEdit
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.plan_ref import ExperimentPlanRef
from scopecat.records.run import ConfigRegistryRunConfigSource
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    UnboundSubject,
)
from scopecat.records.scientific_selection import ReviewedScientificSelection

from scopecat_server.launch_worker import launch


@sc.experiment
def catalog_author(ctx: sc.ExperimentContext, label: str):
    return None


def composed(monkeypatch: pytest.MonkeyPatch) -> tuple[LabApplication, Mock, Mock]:
    monkeypatch.setattr(
        "scopecat_server.launch_worker.resolve_daemon_endpoint",
        Mock(return_value="http://unused"),
    )
    entry = LaunchCatalogEntry(
        id="maintained",
        version="1",
        title="Maintained",
        description="Workflow",
        actions=("preview", "submit"),
        kind="diagnostic",
        configuration_effect="none",
        request=LaunchInputSchema(),
    )

    def callback(
        _lab: LabClient, request: LaunchRequest
    ) -> LaunchCatalog | LaunchPreview:
        return (
            LaunchCatalog(entries=(entry,))
            if request.action == "list"
            else LaunchPreview(
                experiment_id=request.experiment,
                request_hash=request.request_hash,
                point_count=1,
                summary="maintained preview",
                reviewed=ReviewedScientificSelection(
                    binding=ResolvedScientificBinding(
                        subject=UnboundSubject(),
                        config_content_hash="sha256:" + "a" * 64,
                        setup_content_hash="sha256:" + "b" * 64,
                    ),
                    config_source=ConfigRegistryRunConfigSource(
                        selector="active",
                        entry_id="baseline",
                        config_ref="baseline",
                        content_hash="sha256:" + "a" * 64,
                        registry_generation=1,
                    ),
                ),
            )
        )

    maintained = Mock(side_effect=callback)
    lab = Mock()
    application = Mock()
    application.launch_provider = AuthorExperiments(
        (AuthorExperiment.from_declaration(catalog_author),)
    ).compose(maintained)
    application.connect.return_value = nullcontext(lab)
    return cast("LabApplication", application), maintained, lab


def test_composition_lists_once_per_request_and_preserves_maintained_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, maintained, _lab = composed(monkeypatch)
    catalog = launch(application, Path.cwd(), None, LaunchRequest(action="list"))
    assert isinstance(catalog, LaunchCatalog)
    assert {entry.id for entry in catalog.entries} == {"maintained", "catalog_author"}
    assert maintained.call_count == 1
    maintained.reset_mock()
    request = LaunchRequest(action="preview", experiment="maintained", version="1")
    preview = launch(application, Path.cwd(), None, request)
    assert isinstance(preview, LaunchPreview)
    assert preview.summary == "maintained preview"
    assert preview.definition_hash is not None
    assert [call.args[1].action for call in maintained.call_args_list] == [
        "list",
        "preview",
    ]
    assert maintained.call_args.args[1] is request
    # A subsequent call must resolve again rather than caching mutable project state.
    launch(application, Path.cwd(), None, request)
    assert [call.args[1].action for call in maintained.call_args_list] == [
        "list",
        "preview",
        "list",
        "preview",
    ]


def test_authored_dispatch_uses_the_checked_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, maintained, _lab = composed(monkeypatch)
    author = AuthorExperiment.from_declaration(catalog_author)
    with pytest.raises(LaunchRequestRejected, match="label"):
        launch(
            application,
            Path.cwd(),
            None,
            LaunchRequest(
                action="preview",
                experiment=author.entry.id,
                version=author.entry.version,
            ),
        )
    assert [call.args[1].action for call in maintained.call_args_list] == ["list"]


def test_unknown_controls_and_changed_plans_never_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, maintained, lab = composed(monkeypatch)
    request = LaunchRequest(action="preview", experiment="maintained", version="1")
    with pytest.raises(LaunchRequestRejected, match="unknown control"):
        launch(
            application,
            Path.cwd(),
            None,
            request.model_copy(
                update={
                    "control_edits": {"missing": ControlEdit(mode="fixed", value=1.0)},
                }
            ),
        )
    assert [call.args[1].action for call in maintained.call_args_list] == ["list"]
    maintained.reset_mock()
    lab.plans.get.return_value.definition.definition_hash = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="saved plan definition changed"):
        launch(
            application,
            Path.cwd(),
            None,
            request.model_copy(
                update={
                    "plan_ref": ExperimentPlanRef(
                        plan_id="saved", revision=1, content_hash="sha256:" + "a" * 64
                    ),
                }
            ),
        )
    assert [call.args[1].action for call in maintained.call_args_list] == ["list"]
