"""Controller-owned phase timings for serial pytest and xdist runs."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--timing-report", help="Write per-test phase timing JSON")


def pytest_configure(config: pytest.Config) -> None:
    # xdist forwards reports to the controller; writing there avoids races and
    # includes setup/teardown costs charged to the test owning the fixture.
    if not hasattr(config, "workerinput"):
        path = config.getoption("--timing-report")
        if path:
            config.pluginmanager.register(
                TimingReport(Path(path)), "scopecat-timing-report"
            )


class TimingReport:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.started = time.monotonic()
        self.collection_ready_seconds: float | None = None
        self.phases: dict[str, dict[str, float]] = defaultdict(dict)
        self.outcomes: dict[str, dict[str, str]] = defaultdict(dict)

    def pytest_collection_finish(self) -> None:
        self.collection_ready_seconds = time.monotonic() - self.started

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self) -> None:
        # Includes worker startup; record when the last collection arrives.
        self.collection_ready_seconds = time.monotonic() - self.started

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.phases[report.nodeid][report.when] = report.duration
        self.outcomes[report.nodeid][report.when] = report.outcome

    def pytest_sessionfinish(self, exitstatus: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tests = [
            {
                "nodeid": node,
                "phases": phases,
                "outcomes": self.outcomes[node],
                "total_seconds": sum(phases.values()),
            }
            for node, phases in sorted(
                self.phases.items(), key=lambda item: -sum(item[1].values())
            )
        ]
        self.path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "exitstatus": exitstatus,
                    "wall_seconds": time.monotonic() - self.started,
                    "collection_ready_seconds": self.collection_ready_seconds,
                    "tests": tests,
                },
                indent=2,
            )
            + "\n"
        )
