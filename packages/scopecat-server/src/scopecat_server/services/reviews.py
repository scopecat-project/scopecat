"""Process-local coordination for live experiment review workers."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import uuid4

from scopecat.daemon.reviews import (
    ReviewCompilationResult,
    ReviewCompileCommand,
    ReviewCompileReceipt,
    ReviewCompletionCommand,
    ReviewHeartbeatReceipt,
    ReviewSessionCloseReceipt,
    ReviewSessionCreateCommand,
    ReviewSessionListView,
    ReviewSessionView,
    ReviewWorkItem,
)

from ..errors import BackendConflict, BackendNotFound

_DEFAULT_REVIEW_WORKER_TTL = timedelta(seconds=15)
_REVIEW_INACTIVE_SESSION_LIMIT = 32


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class _ReviewSession:
    command: ReviewSessionCreateCommand
    created_at: datetime
    updated_at: datetime
    worker_renewed_at: datetime
    heartbeat_interval_seconds: float
    active: bool = True
    pending: deque[ReviewWorkItem] = field(default_factory=deque)
    claimed_request_ids: set[str] = field(default_factory=set)
    superseded_request_ids: set[str] = field(default_factory=set)
    latest_request: ReviewWorkItem | None = None
    latest_result: ReviewCompilationResult | None = None


class ReviewService:
    """Coordinate a GUI with a notebook-owned pure compiler."""

    def __init__(
        self,
        *,
        worker_ttl: timedelta = _DEFAULT_REVIEW_WORKER_TTL,
        inactive_session_limit: int = _REVIEW_INACTIVE_SESSION_LIMIT,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if worker_ttl <= timedelta(0):
            raise ValueError("review worker TTL must be positive")
        if inactive_session_limit < 0:
            raise ValueError("inactive review session limit must be non-negative")
        self._lock = Lock()
        self._sessions: dict[str, _ReviewSession] = {}
        self._worker_ttl = worker_ttl
        self._inactive_session_limit = inactive_session_limit
        self._clock = clock or _utc_now

    def create(self, command: ReviewSessionCreateCommand) -> ReviewSessionView:
        now = self._clock()
        with self._lock:
            self._expire_all(now)
            self._prune_inactive()
            if command.session_id in self._sessions:
                raise BackendConflict("review session already exists")
            session = _ReviewSession(
                command=command,
                created_at=now,
                updated_at=now,
                worker_renewed_at=now,
                heartbeat_interval_seconds=self._worker_ttl.total_seconds() / 3,
                latest_result=command.initial_result,
            )
            self._sessions[command.session_id] = session
            return _view(session)

    def list(self) -> ReviewSessionListView:
        with self._lock:
            now = self._clock()
            self._expire_all(now)
            self._prune_inactive()
            sessions = sorted(
                self._sessions.values(),
                key=lambda session: session.updated_at,
                reverse=True,
            )
            return ReviewSessionListView(items=tuple(_view(item) for item in sessions))

    def get(self, session_id: str) -> ReviewSessionView:
        with self._lock:
            session = self._require(session_id)
            self._expire_stale(session, self._clock())
            self._prune_inactive()
            return _view(session)

    def enqueue(
        self,
        session_id: str,
        command: ReviewCompileCommand,
    ) -> ReviewCompileReceipt:
        with self._lock:
            now = self._clock()
            session = self._require_active(session_id, now)
            request_id = uuid4().hex
            session.pending.clear()
            session.superseded_request_ids.update(session.claimed_request_ids)
            session.pending.append(
                ReviewWorkItem(
                    session_id=session_id,
                    request_id=request_id,
                    point_index=command.point_index,
                    coordinates=command.coordinates,
                    coordinate_mode=command.coordinate_mode,
                    inspection_query=command.inspection_query,
                )
            )
            session.latest_request = session.pending[-1]
            session.updated_at = now
            return ReviewCompileReceipt(
                session_id=session_id,
                request_id=request_id,
            )

    def claim(self, session_id: str, worker_id: str) -> ReviewWorkItem | None:
        with self._lock:
            session = self._require_worker(session_id, worker_id)
            now = self._clock()
            self._expire_stale(session, now)
            if not session.active or not session.pending:
                self._prune_inactive()
                return None
            self._renew_worker(session, now)
            item = session.pending.popleft()
            session.claimed_request_ids.add(item.request_id)
            session.updated_at = now
            return item

    def complete(
        self,
        session_id: str,
        command: ReviewCompletionCommand,
    ) -> ReviewSessionView:
        with self._lock:
            session = self._require_worker(session_id, command.worker_id)
            request_id = command.result.request_id
            if request_id not in session.claimed_request_ids:
                raise BackendConflict("review request is not claimed by this worker")
            now = self._clock()
            if not session.active:
                raise BackendConflict("review session is closed")
            session.claimed_request_ids.remove(request_id)
            if request_id in session.superseded_request_ids:
                session.superseded_request_ids.remove(request_id)
            else:
                session.latest_result = command.result
            self._renew_worker(session, now)
            session.updated_at = now
            return _view(session)

    def heartbeat(self, session_id: str, worker_id: str) -> ReviewHeartbeatReceipt:
        with self._lock:
            session = self._require_worker(session_id, worker_id)
            now = self._clock()
            self._expire_stale(session, now)
            if session.active:
                self._renew_worker(session, now)
                session.updated_at = now
            receipt = ReviewHeartbeatReceipt(
                session_id=session_id,
                active=session.active,
                updated_at=session.updated_at,
            )
            self._prune_inactive()
            return receipt

    def close(self, session_id: str, worker_id: str) -> ReviewSessionCloseReceipt:
        with self._lock:
            session = self._require_worker(session_id, worker_id)
            closed_at = self._clock()
            self._deactivate(session, closed_at)
            receipt = ReviewSessionCloseReceipt(
                session_id=session_id,
                closed_at=closed_at,
            )
            self._prune_inactive()
            return receipt

    def _require(self, session_id: str) -> _ReviewSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise BackendNotFound(f"unknown review session {session_id!r}")
        return session

    def _require_active(self, session_id: str, now: datetime) -> _ReviewSession:
        session = self._require(session_id)
        self._expire_stale(session, now)
        if not session.active:
            self._prune_inactive()
            raise BackendConflict("review session is closed")
        return session

    def _require_worker(self, session_id: str, worker_id: str) -> _ReviewSession:
        session = self._require(session_id)
        if session.command.worker_id != worker_id:
            raise BackendConflict("review worker does not own this session")
        return session

    def _expire_stale(self, session: _ReviewSession, now: datetime) -> None:
        if session.active and now - session.worker_renewed_at >= self._worker_ttl:
            self._deactivate(session, now)

    def _expire_all(self, now: datetime) -> None:
        for session in self._sessions.values():
            self._expire_stale(session, now)

    def _prune_inactive(self) -> None:
        inactive = sorted(
            (session for session in self._sessions.values() if not session.active),
            key=lambda session: session.updated_at,
            reverse=True,
        )
        for session in inactive[self._inactive_session_limit :]:
            self._sessions.pop(session.command.session_id, None)

    @staticmethod
    def _renew_worker(session: _ReviewSession, now: datetime) -> None:
        session.worker_renewed_at = now

    @staticmethod
    def _deactivate(session: _ReviewSession, now: datetime) -> None:
        session.active = False
        session.pending.clear()
        session.claimed_request_ids.clear()
        session.superseded_request_ids.clear()
        session.updated_at = now


def _view(session: _ReviewSession) -> ReviewSessionView:
    latest_result = session.latest_result
    command = session.command
    return ReviewSessionView(
        session_id=command.session_id,
        title=command.title,
        experiment_id=command.experiment_id,
        experiment_kind=command.experiment_kind,
        active=session.active,
        created_at=session.created_at,
        updated_at=session.updated_at,
        heartbeat_interval_seconds=session.heartbeat_interval_seconds,
        coordinates=command.coordinates,
        planned_points=command.planned_points,
        planned_points_truncated=command.planned_points_truncated,
        pending_request_count=(
            len(session.pending)
            + len(session.claimed_request_ids - session.superseded_request_ids)
        ),
        latest_request=session.latest_request,
        latest_result=latest_result,
    )


__all__ = ["ReviewService"]
