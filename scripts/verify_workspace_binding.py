"""Verify workspace switching and restored evidence in installed Python."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from scopecat.daemon.client import DaemonClient
from scopecat.daemon.endpoint import DAEMON_URL_ENV, DaemonEndpointError
from scopecat.project import open_project
from scopecat_server.lifecycle import (  # noqa: TID251 - installed integration journey
    DaemonLifecycleError,
    initialize_project,
    open_project_gui,
    start_project,
    stop_project,
)
from scopecat_server.snapshots import (  # noqa: TID251 - installed integration journey
    create_snapshot,
    restore_snapshot,
)


@dataclass(frozen=True)
class Summary:
    mean: float
    points: int


def check() -> None:
    os.environ.pop(DAEMON_URL_ENV, None)
    with tempfile.TemporaryDirectory(prefix="scopecat-binding-") as temporary:
        root = Path(temporary).resolve()
        a, b, data, bench = (root / name for name in ("a", "b", "data", "bench"))
        first = initialize_project(a)
        (a / "scopecat.runtime.toml").write_text(
            '[runtime]\ndata_root = "../data"\ndeployment_root = "../bench"\n',
            encoding="utf-8",
        )
        shutil.copytree(a, b)
        second = open_project(b)
        endpoint = start_project(first, timeout=90)
        try:
            # A leftover local record can point at a port now serving another
            # workspace. Check live ownership, not only the recorded paths.
            stale_root = root / "stale"
            stale = initialize_project(stale_root)
            stale_data = stale_root / ".scopecat"
            stale_data.mkdir(exist_ok=True)
            stale_record = endpoint.model_copy(
                update={
                    "project_root": stale_root,
                    "data_root": stale_data,
                    "deployment_root": stale_data,
                }
            )
            (stale_data / "daemon.json").write_text(
                stale_record.model_dump_json(), encoding="utf-8"
            )
            for connect in (
                second.authoring,
                lambda: second.authoring(endpoint.base_url),
                stale.authoring,
            ):
                try:
                    connect()
                except DaemonEndpointError:
                    pass
                else:
                    raise AssertionError("another mutable workspace was allowed")
            try:
                start_project(second, timeout=90)
            except DaemonLifecycleError:
                pass
            else:
                raise AssertionError("competing service was allowed")
            for action in (stop_project, open_project_gui):
                try:
                    action(second)
                except DaemonLifecycleError:
                    pass
                else:
                    raise AssertionError("foreign workspace lifecycle action allowed")
            with first.authoring() as author:
                job = (
                    author.prepare(
                        "signal",
                        inputs={"center": 0.0},
                        scans={"position": [-1.0, 0.0, 1.0]},
                    )
                    .run()
                    .wait(timeout=90)
                )
                run = job.result()
                run_id, receipt_name = run.id, job.receipt.name
                original_revision = author.state().active
                assert (
                    author.analyze_as(
                        run_id, "scopecat_lab.authored.signal:summarize", Summary
                    ).value.mean
                    == 2 / 3
                )
            with DaemonClient(endpoint.base_url) as client:
                before = client.measurement_preview(run_id)
                config = client.run_config(run_id)
                identity = client.health().project_id
        finally:
            stop_project(first)
        shutil.rmtree(a)
        endpoint = start_project(second, timeout=90)
        try:
            with DaemonClient(endpoint.base_url) as client:
                assert client.health().project_id == identity
                assert client.measurement_preview(run_id) == before
                assert client.run_config(run_id) == config
            with second.authoring() as author:
                assert (
                    author.reopen(data / "author-jobs" / receipt_name).result().id
                    == run_id
                )
                assert author.state().active == original_revision
                source = b / "src/scopecat_lab/authored/signal.py"
                source.write_text(
                    source.read_text(encoding="utf-8").replace(
                        "mean=sum(values) / len(values)",
                        "mean=1 + sum(values) / len(values)",
                    ),
                    encoding="utf-8",
                )
                assert author.refresh().active != original_revision
                cases: tuple[tuple[Literal["original", "current"], float], ...] = (
                    ("original", 2 / 3),
                    ("current", 1 + 2 / 3),
                )
                for selected, expected in cases:
                    assert (
                        author.analyze_as(
                            run_id,
                            "scopecat_lab.authored.signal:summarize",
                            Summary,
                            source=selected,
                        ).value.mean
                        == expected
                    )
        finally:
            stop_project(second)
        snapshot, restored = root / "snapshot", root / "restored"
        create_snapshot(second, snapshot)
        for path in (b, data, bench):
            shutil.rmtree(path)
        restore_snapshot(snapshot, restored)
        recovered = open_project(restored)
        endpoint = start_project(recovered, timeout=90)
        try:
            with recovered.authoring() as author:
                job = author.reopen(
                    recovered.runtime_binding.data_root / "author-jobs" / receipt_name
                )
                assert job.result().id == run_id
                assert (
                    author.analyze_as(
                        run_id,
                        "scopecat_lab.authored.signal:summarize",
                        Summary,
                        source="original",
                    ).value.mean
                    == 2 / 3
                )
            with DaemonClient(endpoint.base_url) as client:
                assert client.health().project_id == identity
                assert client.measurement_preview(run_id) == before
        finally:
            stop_project(recovered)
    print(
        "workspace binding verified: A/B ownership, history, receipts, "
        "refresh and restore"
    )


if __name__ == "__main__":
    check()
