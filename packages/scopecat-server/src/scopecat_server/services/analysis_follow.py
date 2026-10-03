"""Bounded complete-group discovery and retained author analysis execution."""

import logging
from collections.abc import Callable
from threading import Event, Lock, Thread

from scopecat.analysis.group_completion import planned_groups
from scopecat.records.analysis_follow import AnalysisFollowEvent
from scopecat.records.author_revision import (
    AuthorAnalysisReceipt,
    AuthorAnalysisRequest,
)
from scopecat.records.measurement_recording import (
    CANONICAL_MEASUREMENT_DATASET_REF,
    MeasurementDatasetHeader,
)

from scopecat_server.storage.sqlite.analysis_follow import AnalysisFollowRepository
from scopecat_server.storage.sqlite.measurement_slices import freeze_measurement_slice
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository

_LOG = logging.getLogger(__name__)
type AnalyzeGroup = Callable[
    [AuthorAnalysisRequest, float, Event], AuthorAnalysisReceipt
]


class AnalysisFollowRunner:
    """One bounded background call at a time, sharing retained source workers."""

    def __init__(
        self,
        repository: AnalysisFollowRepository,
        runs: SQLiteRunRepository,
        analyze: AnalyzeGroup,
    ) -> None:
        self.repository = repository
        self.runs = runs
        self._analyze = analyze
        self._stop = Event()
        self._lock = Lock()
        self._active: tuple[str, Event] | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        # A publication may have succeeded just before a process died. Preserve
        # its evidence and require inspection; do not silently execute it twice.
        for identity in self.repository.running():
            for index, state in self.repository.states(identity).items():
                if state == "running":
                    self.repository.event(
                        identity,
                        AnalysisFollowEvent(
                            cursor=0,
                            group_index=index,
                            state="uncertain",
                            error=(
                                "Application stopped during analysis; inspect retained "
                                "publications before starting another follow."
                            ),
                        ),
                    )
                    self.repository.set_state(
                        identity,
                        "attention",
                        "Interrupted analysis may have published a result",
                    )
        self._thread = Thread(target=self._run, name="analysis-follows", daemon=True)
        self._thread.start()

    def cancel(self, identity: str) -> None:
        with self._lock:
            if self._active is not None and self._active[0] == identity:
                self._active[1].set()
            else:
                self.repository.set_state(identity, "stopped")

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._active is not None:
                self._active[1].set()
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            for identity in self.repository.running():
                if self._stop.is_set():
                    return
                try:
                    self.advance(identity)
                except Exception as error:
                    _LOG.exception("Group analysis failed: %s", identity)
                    self.repository.set_state(identity, "attention", str(error))

    def advance(self, identity: str) -> None:
        view = self.repository.get(identity)
        if view.state != "running":
            return
        request = view.request
        run_id = request.analysis.run_id
        run = self.runs.read_snapshot(run_id)
        header_ref = f"{CANONICAL_MEASUREMENT_DATASET_REF}/header.json"
        if not self.runs.exists(run_id, header_ref):
            if run.outcome is not None:
                self.repository.set_state(
                    identity,
                    "attention",
                    "Acquisition ended without a measurement contract",
                )
            return
        header = self.runs.read_model(run_id, header_ref, MeasurementDatasetHeader)
        grouping = request.analysis.grouping
        assert grouping is not None
        offset = self.repository.scan(identity)
        groups = tuple(
            planned_groups(
                header.dataset_schema,
                grouping,
                max_groups=request.max_groups,
                max_points=request.max_points_per_group,
                offset=offset,
                limit=32,
            )
        )
        states = self.repository.states(identity)
        for index, group in enumerate(groups, offset):
            if index in states:
                continue
            selected = freeze_measurement_slice(
                self.runs,
                run_id,
                group.point_indices,
                max_input_bytes=request.max_input_bytes,
            )
            if selected is None:
                if run.outcome is not None:
                    self.repository.event(
                        identity,
                        AnalysisFollowEvent(
                            cursor=0,
                            group_index=index,
                            state="incomplete",
                            error=(
                                "Acquisition ended before every expected "
                                "group point was committed"
                            ),
                        ),
                    )
                continue
            self.repository.advance_scan(identity, index + 1)
            event = AnalysisFollowEvent(
                cursor=0,
                group_index=index,
                state="running",
                measurement_slice=selected.id,
            )
            with self._lock:
                if (
                    self._stop.is_set()
                    or self.repository.get(identity).state != "running"
                ):
                    return
                cancelled = Event()
                self._active = (identity, cancelled)
            try:
                self.repository.event(identity, event)
                result = self._analyze(
                    request.analysis.model_copy(
                        update={
                            "measurement_slice": selected.id,
                            "key": f"follow/{identity}/{index}",
                        }
                    ),
                    request.timeout_seconds,
                    cancelled,
                )
                if len(result.groups) != 1:
                    raise ValueError(
                        "fixed group did not produce exactly one analysis receipt"
                    )
                receipt = result.groups[0]
                self.repository.event(
                    identity,
                    event.model_copy(
                        update={
                            "state": "failed" if receipt.error else "succeeded",
                            "receipt": receipt,
                            "error": receipt.error,
                        }
                    ),
                )
            except Exception as error:
                self.repository.event(
                    identity,
                    event.model_copy(
                        update={"state": "uncertain", "error": str(error)}
                    ),
                )
                self.repository.set_state(
                    identity,
                    "stopped"
                    if cancelled.is_set() and not self._stop.is_set()
                    else "attention",
                    "Analysis outcome is uncertain; inspect retained publications",
                )
            finally:
                with self._lock:
                    if cancelled.is_set() and not self._stop.is_set():
                        self.repository.set_state(identity, "stopped")
                    self._active = None
            return
        end = offset + len(groups)
        self.repository.advance_scan(
            identity, 0 if len(groups) < 32 else end, end if len(groups) < 32 else None
        )
        latest = self.repository.get(identity)
        if (
            latest.group_count is not None
            and latest.finished_count == latest.group_count
        ):
            self.repository.set_state(identity, "completed")
