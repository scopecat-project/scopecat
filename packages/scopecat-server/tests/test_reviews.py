from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.reviews import (
    ReviewCompilationResult,
    ReviewCompileCommand,
    ReviewCompletionCommand,
    ReviewCoordinateSpec,
    ReviewPointView,
    ReviewSessionCreateCommand,
)
from scopecat.inspection import CompiledProgramInspectionQuery
from scopecat.kernel.quantity import Quantity

from scopecat_server import BackendConflict, BackendNotFound, LocalDaemonRuntime
from scopecat_server.services.reviews import ReviewService


def _review_command(index: int) -> ReviewSessionCreateCommand:
    return ReviewSessionCreateCommand(
        session_id=f"review-{index}",
        worker_id=f"worker-{index}",
        title=f"Review {index}",
        experiment_id=f"experiment-{index}",
        experiment_kind="experiment",
        coordinates=(),
    )


def test_review_worker_lease_expires_without_losing_latest_result() -> None:
    now = [datetime(2026, 8, 14, tzinfo=UTC)]
    service = ReviewService(
        worker_ttl=timedelta(seconds=15),
        clock=lambda: now[0],
    )
    initial_result = ReviewCompilationResult(
        request_id="initial",
        completed_at=now[0],
    )
    session = service.create(
        ReviewSessionCreateCommand(
            session_id="review-lease",
            worker_id="worker-lease",
            title="Lease review",
            experiment_id="lease-review",
            experiment_kind="experiment",
            coordinates=(),
            initial_result=initial_result,
        )
    )
    assert session.heartbeat_interval_seconds == 5
    now[0] += timedelta(seconds=10)
    assert service.heartbeat(session.session_id, "worker-lease").active
    now[0] += timedelta(seconds=10)
    receipt = service.enqueue(
        session.session_id,
        ReviewCompileCommand(
            coordinates={},
            coordinate_mode="free",
            inspection_query=CompiledProgramInspectionQuery(
                layer_id="scheduled",
                entity_id="q17",
                offset=128,
                limit=128,
            ),
        ),
    )
    claimed = service.claim(session.session_id, "worker-lease")
    assert claimed is not None
    assert claimed.request_id == receipt.request_id
    assert claimed.inspection_query is not None
    assert claimed.inspection_query.entity_id == "q17"
    assert claimed.inspection_query.offset == 128
    assert service.get(session.session_id).pending_request_count == 1

    now[0] += timedelta(seconds=16)
    expired = service.get(session.session_id)

    assert not expired.active
    assert expired.pending_request_count == 0
    assert expired.latest_result == initial_result
    assert expired.latest_request == claimed
    assert not service.heartbeat(session.session_id, "worker-lease").active
    with pytest.raises(BackendConflict, match="closed"):
        service.enqueue(
            session.session_id,
            ReviewCompileCommand(coordinates={}, coordinate_mode="free"),
        )


def test_review_service_retains_only_recent_inactive_sessions() -> None:
    now = [datetime(2026, 8, 14, tzinfo=UTC)]
    service = ReviewService(
        inactive_session_limit=2,
        clock=lambda: now[0],
    )
    for index in range(3):
        command = _review_command(index)
        service.create(command)
        service.close(command.session_id, command.worker_id)
        now[0] += timedelta(seconds=1)

    assert [item.session_id for item in service.list().items] == [
        "review-2",
        "review-1",
    ]
    with pytest.raises(BackendNotFound, match="unknown review session"):
        service.get("review-0")

    active = _review_command(3)
    service.create(active)
    assert service.get(active.session_id).active


def test_new_review_query_supersedes_queued_and_claimed_results() -> None:
    now = datetime(2026, 8, 14, tzinfo=UTC)
    service = ReviewService(clock=lambda: now)
    session = service.create(_review_command(0))
    first = service.enqueue(
        session.session_id,
        ReviewCompileCommand(point_index=0),
    )
    claimed = service.claim(session.session_id, "worker-0")
    assert claimed is not None
    assert claimed.request_id == first.request_id

    second = service.enqueue(
        session.session_id,
        ReviewCompileCommand(
            point_index=0,
            inspection_query=CompiledProgramInspectionQuery(
                layer_id="scheduled",
                text="measure",
            ),
        ),
    )
    assert service.get(session.session_id).pending_request_count == 1

    stale = service.complete(
        session.session_id,
        ReviewCompletionCommand(
            worker_id="worker-0",
            result=ReviewCompilationResult(
                request_id=first.request_id,
                completed_at=now,
                error="stale",
            ),
        ),
    )
    assert stale.latest_result is None
    assert stale.pending_request_count == 1

    latest = service.claim(session.session_id, "worker-0")
    assert latest is not None
    assert latest.request_id == second.request_id


def test_review_session_round_trips_compile_work_without_run_admission(
    tmp_path: Path,
) -> None:
    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(transport) as client,
    ):
        session = client.create_review(
            ReviewSessionCreateCommand(
                session_id="review-1",
                worker_id="worker-1",
                title="DRAG beta",
                experiment_id="drag-beta",
                experiment_kind="experiment",
                coordinates=(
                    ReviewCoordinateSpec(
                        id="beta",
                        kind="quantity",
                        unit="ns",
                        sampled_values=(Quantity(0.0, "ns"),),
                    ),
                ),
            )
        )

        receipt = client.enqueue_review_compile(
            session.session_id,
            ReviewCompileCommand(
                coordinates={"beta": Quantity(0.137, "ns")},
                coordinate_mode="free",
            ),
        )
        work = client.claim_review_work(session.session_id, "worker-1")
        assert work is not None
        assert work.request_id == receipt.request_id
        assert work.coordinates == {"beta": Quantity(0.137, "ns")}

        completed = client.complete_review_work(
            session.session_id,
            ReviewCompletionCommand(
                worker_id="worker-1",
                result=ReviewCompilationResult(
                    request_id=work.request_id,
                    completed_at=datetime.now(UTC),
                    point=ReviewPointView(
                        coordinates={"beta": Quantity(0.137, "ns")},
                        source="operator",
                    ),
                ),
            ),
        )

        assert completed.pending_request_count == 0
        assert completed.latest_result is not None
        assert completed.latest_result.point is not None
        assert completed.latest_result.point.point_index is None
        assert (
            runtime.application.runs.list_runs(
                limit=10,
                before=None,
                state=None,
            ).items
            == ()
        )


def _daemon_client(transport: TestClient) -> DaemonClient:
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

    return DaemonClient(
        "http://testserver",
        transport=httpx2.MockTransport(send),
    )


@pytest.mark.parametrize("stale_finishes_first", [True, False])
def test_shared_review_request_identity_survives_interleaved_completions(
    stale_finishes_first: bool,
) -> None:
    now = datetime(2026, 8, 14, tzinfo=UTC)
    initial = ReviewCompilationResult(request_id="initial", completed_at=now)
    service = ReviewService(clock=lambda: now)
    command = _review_command(0).model_copy(update={"initial_result": initial})
    service.create(command)
    first = service.enqueue(command.session_id, ReviewCompileCommand(point_index=0))
    service.claim(command.session_id, command.worker_id)
    service.enqueue(command.session_id, ReviewCompileCommand(point_index=1))
    latest = service.enqueue(command.session_id, ReviewCompileCommand(point_index=2))
    work = service.claim(command.session_id, command.worker_id)
    assert work is not None and work.request_id == latest.request_id
    waiting = service.get(command.session_id)
    assert waiting.latest_request == work
    assert waiting.latest_result == initial
    assert waiting.pending_request_count == 1
    assert service.list().items[0] == waiting
    results = [
        ReviewCompilationResult(
            request_id=first.request_id, completed_at=now, error="stale"
        ),
        ReviewCompilationResult(request_id=latest.request_id, completed_at=now),
    ]
    if not stale_finishes_first:
        results.reverse()
    for result in results:
        state = service.complete(
            command.session_id,
            ReviewCompletionCommand(
                worker_id=command.worker_id,
                result=result,
            ),
        )
        assert state.latest_request == work
        if result.request_id == first.request_id and stale_finishes_first:
            assert state.latest_result == initial
            assert state.pending_request_count == 1
    finished = service.get(command.session_id)
    assert finished.latest_result is not None
    assert finished.latest_result.request_id == latest.request_id
    assert finished.pending_request_count == 0
