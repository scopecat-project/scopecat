# pyright: reportAny=false
"""Bounded residency record contracts; real process measurements remain opt-in."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import psutil
import pytest

from benchmarks.e2e import author_residency
from scopecat.records.author_revision import AuthorAnalysisReceipt, AuthorRevisionRef


@pytest.mark.parametrize(("rounds", "operations"), [(1, 1), (2, 3)])
@pytest.mark.parametrize("extended", [False, True])
def test_residency_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rounds: int,
    operations: int,
    extended: bool,
) -> None:
    pools: dict[str, dict[str, MagicMock]] = {"prepare": {}, "analysis": {}}
    next_pid = 100
    revision_number = 0
    run_ids: list[str] = []
    analysis_inputs: list[str] = []
    original = AuthorRevisionRef(content_hash=f"sha256:{0:064x}")
    active = original

    def worker(role: str, revision: AuthorRevisionRef) -> None:
        nonlocal next_pid
        pool = pools[role]
        key = revision.content_hash
        if key in pool:
            return
        if len(pool) == 2:
            del pool[next(iter(pool))]
        next_pid += 1
        process = MagicMock(pid=next_pid)
        process.create_time.return_value = float(next_pid)
        module = (
            "scopecat_server.validation_worker"
            if role == "prepare"
            else "scopecat_server.retained_worker"
        )
        process.cmdline.return_value = ["python", "-m", module, key]
        process.memory_info.return_value.rss = 1024
        pool[key] = process

    def prepare(
        *_args: object,
        code_revision: AuthorRevisionRef | None = None,
        **_kwargs: object,
    ) -> MagicMock:
        if extended and code_revision is not None:
            assert _kwargs["scans"] == {"frequency": [4.7, 4.8, 4.9]}
        revision = code_revision or active
        worker("prepare", revision)
        result = MagicMock()
        result.preview.code_revision = revision

        def run() -> MagicMock:
            run_id = f"retained-run-{len(run_ids) + 1}"
            run_ids.append(run_id)
            job = MagicMock()
            job.wait.return_value.result.return_value.id = run_id
            return job

        result.run.side_effect = run
        return result

    def analyze(
        run_id: str, _name: str, *, code_revision: AuthorRevisionRef
    ) -> AuthorAnalysisReceipt:
        analysis_inputs.append(run_id)
        worker("analysis", code_revision)
        return AuthorAnalysisReceipt(code_revision=code_revision, analysis_id="receipt")

    def refresh() -> MagicMock:
        nonlocal revision_number, active
        revision_number += 1
        active = AuthorRevisionRef(content_hash=f"sha256:{revision_number:064x}")
        worker("prepare", active)
        return MagicMock(active=active)

    author = MagicMock()
    author.__enter__.return_value = author
    author.prepare.side_effect = prepare
    author.analyze.side_effect = analyze
    author.refresh.side_effect = refresh
    owner = MagicMock(pid=1)
    owner.create_time.return_value = 1.0
    owner.cmdline.return_value = ["daemon"]
    owner.memory_info.return_value.rss = 1024

    def children(*, recursive: bool) -> list[MagicMock]:
        assert recursive
        return [process for pool in pools.values() for process in pool.values()]

    owner.children.side_effect = children
    stop = MagicMock()
    monkeypatch.setattr(
        author_residency, "AuthorProject", MagicMock(return_value=author)
    )
    monkeypatch.setattr(author_residency, "select_author_context", MagicMock())
    monkeypatch.setattr(author_residency, "load_project", MagicMock())
    monkeypatch.setattr(author_residency, "start_project", MagicMock())
    monkeypatch.setattr(author_residency, "stop_project", stop)
    monkeypatch.setattr(psutil, "Process", MagicMock(return_value=owner))
    monkeypatch.setattr(psutil, "wait_procs", MagicMock(return_value=([], [])))
    result = author_residency.measure(
        tmp_path,
        revisions=3,
        rounds=rounds,
        operations_per_revision=operations,
        run_per_visit=extended,
        concurrent_analysis=extended,
    )
    records = cast("list[dict[str, object]]", result["operations"])
    samples = cast("list[dict[str, object]]", result["samples"])
    counts = Counter(str(record["operation"]).split("_")[0] for record in records)
    expected = max(2, operations) + rounds * 3 * operations
    assert counts == {
        "prepare": expected,
        "analysis": expected,
        "refresh": 2 * rounds,
        "virtual": 1 + (rounds * 3 + (operations > 2) if extended else 0),
    }
    assert result["case_version"] == 5
    assert len(cast("list[str]", result["revision_hashes"])) == 1 + 2 * rounds
    assert result["run_id"] == run_ids[-1]
    assert len(run_ids) == counts["virtual"]
    latest_run: str | None = None
    expected_analysis_inputs: list[str] = []
    for record in records:
        if record["operation"] == "virtual_run":
            latest_run = cast("str", record["run_id"])
        if "overlapping_analysis_id" in record:
            # Overlap finishes before this visit's new run is submitted.
            assert record["overlapping_run_id"] == latest_run
            assert latest_run is not None
            expected_analysis_inputs.append(latest_run)
        if str(record["operation"]).startswith("analysis"):
            assert record["run_id"] == latest_run
            assert latest_run is not None
            expected_analysis_inputs.append(latest_run)
    assert analysis_inputs == expected_analysis_inputs
    assert result["survivors"] == []
    assert all(record["revision"] for record in records)
    assert all(
        record["analysis_id"] == "receipt"
        for record in records
        if str(record["operation"]).startswith("analysis")
    )
    for index, record in enumerate(records):
        checkpoints = [
            sample
            for sample in samples
            if sample["operation_index"] == index
            and sample["operation"] == record["operation"]
        ]
        assert len(checkpoints) == 1
        role = str(record["operation"]).split("_")[0]
        if role in {"prepare", "analysis"}:
            processes = cast("list[dict[str, object]]", checkpoints[0]["processes"])
            assert any(
                process["role"] == role and process["revision"] == record["revision"]
                for process in processes
            )
    assert (
        sum(record["phase"] == "restore" for record in records)
        == (3 if extended else 2) * rounds
    )
    stop.assert_called_once()
    assert author.prepare.call_count == expected
    assert author.analyze.call_count == expected + (
        rounds * 3 * operations + max(0, operations - 2) if extended else 0
    )


@pytest.mark.parametrize(
    ("revisions", "rounds", "operations"), [(2, 1, 1), (3, 0, 1), (3, 1, 0), (3, -1, 1)]
)
def test_invalid_workload_has_no_side_effects(
    tmp_path: Path, revisions: int, rounds: int, operations: int
) -> None:
    with pytest.raises(ValueError, match=r"required|positive"):
        author_residency.measure(
            tmp_path,
            revisions=revisions,
            rounds=rounds,
            operations_per_revision=operations,
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "arguments",
    [
        ["--revisions", "2"],
        ["--rounds", "0"],
        ["--operations-per-revision", "-1"],
        ["--rounds", "not-an-integer"],
    ],
)
def test_cli_rejects_before_project_creation(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    import sys
    import tempfile

    temporary = MagicMock()
    monkeypatch.setattr(sys, "argv", ["author-residency", *arguments])
    monkeypatch.setattr(tempfile, "TemporaryDirectory", temporary)
    with pytest.raises(SystemExit) as error:
        author_residency.main()
    assert error.value.code == 2
    temporary.assert_not_called()
