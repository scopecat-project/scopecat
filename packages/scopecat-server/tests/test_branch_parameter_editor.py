"""The table editor owns only parameters; branch conflicts preserve local edits."""

from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from scopecat.application.author_project import AuthorProject
from scopecat.daemon.client import DaemonConflictError, DaemonNotFoundError
from scopecat.daemon.wire import SampleCreateCommand
from scopecat.kernel.errors import Conflict
from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot
from scopecat.records.sample import SampleRevisionDraft

from scopecat_server.runtime import LocalDaemonRuntime


@pytest.fixture
def session(tmp_path: Path) -> Iterator[AuthorProject]:
    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
    ):

        def send(request: httpx2.Request) -> httpx2.Response:
            response = transport.request(
                request.method,
                request.url.raw_path.decode(),
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx2.Response(
                response.status_code,
                content=response.content,
                headers=dict(response.headers),
            )

        with AuthorProject(
            "http://testserver", transport=httpx2.MockTransport(send)
        ) as author:
            seed = author.parameters.save(
                name="empty",
                catalog=ParameterCatalog(id="author"),
                parameters=ParameterSnapshot(id="values"),
            )
            author.parameters.create_branch("daily", revision=seed)
            yield author
            assert not runtime.application.setup.list()
            assert not runtime.application.config.get_config_registry().entries


def test_table_structure_save_copy_rebase_and_fork(session: AuthorProject) -> None:
    session.use(parameter_branch="daily")
    params = session.params
    table = params.declare_table(
        "drive", key="id", columns={"id": str, "frequency": float, "amplitude": float}
    )
    table["q0"] = {"frequency": 5.0, "amplitude": 0.2}
    saved = params.save()
    assert session.parameter_branch.head.revision == saved.ref
    assert params.save() == saved
    assert not params.diff()
    assert "Saved · parameter revision" in table.render_html()
    local = params.copy()
    row = local["drive"]["q0"]
    row["frequency"] = 5.1
    params["drive"]["q0"]["amplitude"] = 0.3
    params.save()
    with pytest.raises(DaemonConflictError, match="branch changed"):
        local.save()
    assert row["frequency"] == 5.1
    local.rebase()
    assert row["frequency"] == 5.1
    assert row["amplitude"] == 0.3
    merged = local.save()
    assert session.parameters.get(merged.id) == merged
    local["drive"]["q0"]["frequency"] = 5.2
    detached = local.copy()
    local.discard()
    assert row["frequency"] == 5.1
    assert detached["drive"]["q0"]["frequency"] == 5.2
    fork = detached.save("trial")
    assert detached.branch == "trial"
    assert session.parameters.checkout("daily").head.revision == merged.ref
    assert session.parameters.checkout("trial").head.revision == fork.ref
    assert session.selection.parameter_branch == "daily"


def test_session_edits_before_setup_and_rejects_invalid_selection_atomically(
    session: AuthorProject,
) -> None:
    session.create_sample(
        SampleCreateCommand(
            operation_id="sample",
            sample_id="chip",
            kind="chip",
            actor="author",
            content=SampleRevisionDraft(display_name="Chip"),
        )
    )
    session.use(sample="chip", parameter_branch="daily")
    params = session.params
    params.declare_table("drive", key="id", columns={"id": str, "frequency": float})[
        "q0"
    ] = {"frequency": None}
    revision = params.save()
    selected = session.selection
    with pytest.raises(ValueError, match="reference differs"):
        session.use(
            parameters=revision.ref.model_copy(
                update={"content_hash": "sha256:" + "0" * 64}
            )
        )
    with pytest.raises(DaemonNotFoundError):
        session.use(sample="missing")
    assert session.selection == selected
    assert session.params is params
    # Editing succeeds with incomplete calibration and no executable setup.
    with pytest.raises(DaemonNotFoundError, match="setup"):
        params.preview()
    session.use(parameter_branch=None)
    assert session.selection.parameter_branch is None
    assert session.selection.science.subject == selected.science.subject


def test_unknown_values_can_be_saved_before_calibration(session: AuthorProject) -> None:
    params = session.parameters.workspace("daily")
    params.declare_table("drive", key="id", columns={"id": str, "frequency": float})[
        "q0"
    ] = {"frequency": None}
    saved = params.save()
    reopened = session.parameters.workspace("daily")
    assert reopened["drive"]["q0"]["frequency"] is None
    assert reopened.version == saved
    standalone = session.parameters.save(
        name="uncalibrated", catalog=saved.catalog, parameters=saved.parameters
    )
    assert standalone.parameters == saved.parameters


def test_conflicting_cells_and_structure_change_preserve_draft(
    session: AuthorProject,
) -> None:
    first = session.parameters.workspace("daily")
    first.declare_table("drive", key="id", columns={"id": str, "frequency": float})[
        "q0"
    ] = {"frequency": 5.0}
    first.save()
    second = first.copy()
    first["drive"]["q0"]["frequency"] = 5.1
    second["drive"]["q0"]["frequency"] = 5.2
    first.save()
    before = second.diff()
    with pytest.raises(Conflict):
        second.rebase()
    assert second.diff() == before
    first.rename_column("drive", "frequency", "frequency_ghz")
    first.save()
    with pytest.raises(ValueError, match="schema changed"):
        second.rebase()
    assert second.diff() == before
