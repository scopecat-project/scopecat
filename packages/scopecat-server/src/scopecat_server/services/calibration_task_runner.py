"""Daemon-owned advancement; existing procedure workers own execution."""

import logging
from threading import Event, Thread

from scopecat_server.services.calibration_tasks import CalibrationTaskService
from scopecat_server.services.project_workers import ProjectProcedureWorkers

_LOG = logging.getLogger(__name__)


class CalibrationTaskRunner:
    def __init__(
        self, tasks: CalibrationTaskService, workers: ProjectProcedureWorkers
    ) -> None:
        self._tasks = tasks
        self._workers = workers
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = Thread(target=self._run, name="calibration-tasks", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def tick(self) -> None:
        after = 0
        while not self._stop.is_set():
            page = self._tasks.running_ids(after)
            if not page:
                return
            after = page[-1][0]
            for _, task_id in page:
                if self._stop.is_set():
                    return
                try:
                    view = self._tasks.advance(task_id)
                    if view.task.mode != "running":
                        continue
                    for stage in view.progress.stages:
                        if stage.state == "queued":
                            assert stage.procedure_run_id is not None
                            self._workers.manage(stage.procedure_run_id)
                    if (
                        view.finalization is not None
                        and view.finalization.state == "ready"
                    ):
                        self._workers.manage(view.finalization.procedure_run_id)
                except Exception:
                    _LOG.exception("Calibration task advancement failed: %s", task_id)

    def _run(self) -> None:
        while not self._stop.wait(1):
            try:
                self.tick()
            except Exception:
                _LOG.exception("Calibration task discovery failed")
