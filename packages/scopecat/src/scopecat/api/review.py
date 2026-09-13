"""Live bridge between a notebook-owned compiler and the daemon review GUI."""

from __future__ import annotations

import atexit
from contextlib import suppress
from datetime import UTC, datetime
from threading import Event, Lock, Thread
from typing import Self

from scopecat.authoring.experiments import ExperimentInvocation
from scopecat.control.models import PointCoordinateSpec
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.review_projection import inspection_domain, inspection_point
from scopecat.daemon.reviews import (
    ReviewCompilationResult,
    ReviewCompletionCommand,
    ReviewSessionCreateCommand,
    ReviewSessionView,
    ReviewWorkItem,
)
from scopecat.execution.program import RunProgram
from scopecat.planning.point_selection import point_coordinate_contract
from scopecat.planning.preview import build_run_program_preview
from scopecat.planning.preview_models import ExperimentPreview

_WORKER_POLL_SECONDS = 0.2


class ExperimentReviewHandle:
    """Keep a pure local compiler available to one browser review session."""

    def __init__(
        self,
        *,
        client: DaemonClient,
        program: RunProgram,
        invocation: ExperimentInvocation,
        session: ReviewSessionView,
        worker_id: str,
    ) -> None:
        self._client = client
        self._program = program
        self._invocation = invocation
        self._session = session
        self._worker_id = worker_id
        self._stop = Event()
        self._close_lock = Lock()
        self._closed = False
        self._worker_thread = Thread(
            target=self._serve,
            name=f"scopecat-review-{session.session_id}",
            daemon=True,
        )
        self._heartbeat_thread = Thread(
            target=self._heartbeat,
            name=f"scopecat-review-heartbeat-{session.session_id}",
            daemon=True,
        )
        atexit.register(self._close_at_exit)
        self._worker_thread.start()
        self._heartbeat_thread.start()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def id(self) -> str:
        return self._session.session_id

    @property
    def url(self) -> str:
        return f"{self._client.base_url}/#reviews/{self.id}"

    @property
    def session(self) -> ReviewSessionView:
        return self._client.get_review(self.id)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._stop.set()
        self._worker_thread.join()
        self._heartbeat_thread.join()
        atexit.unregister(self._close_at_exit)
        self._client.close_review_worker(self.id, self._worker_id)

    def _serve(self) -> None:
        try:
            while not self._stop.wait(_WORKER_POLL_SECONDS):
                item = self._client.claim_review_work(self.id, self._worker_id)
                if item is not None:
                    self._complete(item)
        finally:
            self._stop.set()

    def _heartbeat(self) -> None:
        try:
            while not self._stop.wait(self._session.heartbeat_interval_seconds):
                heartbeat = self._client.heartbeat_review_worker(
                    self.id,
                    self._worker_id,
                )
                if not heartbeat.active:
                    return
        finally:
            self._stop.set()

    def _close_at_exit(self) -> None:
        with suppress(Exception):
            self.close()

    def _complete(self, item: ReviewWorkItem) -> None:
        try:
            preview = build_run_program_preview(
                self._program,
                invocation=self._invocation,
                point="first" if item.point_index is None else item.point_index,
                coordinates=item.coordinates,
                coordinate_mode=item.coordinate_mode,
                inspection_query=item.inspection_query,
            )
            result = _review_result(item.request_id, preview)
        except Exception as error:
            result = ReviewCompilationResult(
                request_id=item.request_id,
                completed_at=datetime.now(UTC),
                error=str(error),
            )
        self._client.complete_review_work(
            self.id,
            ReviewCompletionCommand(worker_id=self._worker_id, result=result),
        )


def create_experiment_review(
    *,
    client: DaemonClient,
    program: RunProgram,
    invocation: ExperimentInvocation,
    session_id: str,
    worker_id: str,
    title: str,
) -> ExperimentReviewHandle:
    """Publish one planned experiment and start its local compiler worker."""

    preview = build_run_program_preview(
        program,
        invocation=invocation,
    )
    command = ReviewSessionCreateCommand(
        session_id=session_id,
        worker_id=worker_id,
        title=title,
        experiment_id=program.experiment_id,
        experiment_kind=program.points.experiment_kind,
        coordinates=_coordinate_specs(program),
        planned_points=tuple(inspection_point(point) for point in preview.points),
        planned_points_truncated=preview.points_truncated,
        initial_result=_review_result("initial", preview),
    )
    session = client.create_review(command)
    return ExperimentReviewHandle(
        client=client,
        program=program,
        invocation=invocation,
        session=session,
        worker_id=worker_id,
    )


def _coordinate_specs(program: RunProgram) -> tuple[PointCoordinateSpec, ...]:
    coordinates, _, _ = point_coordinate_contract(program.points)
    return coordinates


def _review_result(
    request_id: str,
    preview: ExperimentPreview,
) -> ReviewCompilationResult:
    return ReviewCompilationResult(
        request_id=request_id,
        completed_at=datetime.now(UTC),
        point=(
            None
            if preview.selected_point is None
            else inspection_point(preview.selected_point)
        ),
        inspections=tuple(
            inspection_domain(inspection) for inspection in preview.domain_inspections
        ),
    )


__all__ = ["ExperimentReviewHandle", "create_experiment_review"]
