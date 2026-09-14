from __future__ import annotations

import os
import shutil
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import pytest
from scopecat.daemon.endpoint import DAEMON_URL_ENV
from scopecat.project import load_project
from scopecat_server.lifecycle import DaemonLifecycleError, start_project, stop_project
from scopecat_testkit.project_loading import isolated_project_imports

EXAMPLE_ROOT = Path(__file__).parents[1]


@dataclass(frozen=True, slots=True)
class ReferenceLabDaemon:
    url: str


@pytest.fixture(autouse=True)
def isolate_project_loader() -> Generator[None]:
    with isolated_project_imports():
        yield


@pytest.fixture(scope="session", autouse=True)
def reference_lab_daemon(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[ReferenceLabDaemon]:
    """Run every notebook against one real HTTP daemon instance."""

    project_root = tmp_path_factory.mktemp("reference-lab-project")
    shutil.copytree(EXAMPLE_ROOT / "config", project_root / "config")
    shutil.copytree(EXAMPLE_ROOT / "src", project_root / "src")
    shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", project_root / "scopecat.toml")
    project = load_project(project_root / "scopecat.toml")
    try:
        record = start_project(project)
    except DaemonLifecycleError as error:
        log = project_root / ".scopecat" / "daemon.log"
        if log.exists():
            error.add_note(
                "Reference fixture daemon log tail:\n"
                + log.read_bytes()[-8192:].decode("utf-8", errors="replace")
            )
        raise
    previous_url = os.environ.get(DAEMON_URL_ENV)
    os.environ[DAEMON_URL_ENV] = record.base_url
    try:
        yield ReferenceLabDaemon(url=record.base_url)
    finally:
        if previous_url is None:
            os.environ.pop(DAEMON_URL_ENV, None)
        else:
            os.environ[DAEMON_URL_ENV] = previous_url
        stop_project(project)
