"""Bounded diagnostic process ownership, evidence summaries and safe log bundles."""

from __future__ import annotations

import json
import math
import statistics
import subprocess
import time
import zipfile
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import cast

import psutil
from pydantic import JsonValue, TypeAdapter

from scopecat_server.validation_process import terminate_validation_process_tree


def write_json(path: Path, value: object) -> None:
    if path.is_symlink():
        raise ValueError("diagnostic output must not be a symlink")
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def run_case(
    command: list[str],
    directory: Path,
    env: dict[str, str],
    timeout: float,
    *,
    cwd: Path,
) -> dict[str, JsonValue]:
    if not command or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("command must be nonempty and timeout positive and finite")

    started = time.monotonic()
    known: dict[int, psutil.Process] = {}
    timed_out = False
    with (directory / "worker.log").open("x", encoding="utf-8") as log:
        try:
            process = subprocess.Popen(  # noqa: S603 - explicit diagnostic command
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except OSError as error:
            message = f"{type(error).__name__}: {error}"
            log.write(message + "\n")
            failed_start: dict[str, JsonValue] = {
                "status": "failed",
                "returncode": None,
                "start_error": message,
                "watchdog_timeout": False,
                "wall_seconds": time.monotonic() - started,
                "residual_before_cleanup": [],
                "survivors": [],
            }
            write_json(directory / "outcome.json", failed_start)
            return failed_start
        owner: psutil.Process | None = None
        with suppress(psutil.NoSuchProcess):
            owner = psutil.Process(process.pid)
            known[owner.pid] = owner
        try:
            while process.poll() is None:
                with suppress(psutil.NoSuchProcess):
                    for child in owner.children(recursive=True) if owner else ():
                        known[child.pid] = child
                if time.monotonic() - started > timeout:
                    timed_out = True
                    terminate_validation_process_tree(process, owner=owner)
                    break
                time.sleep(0.2)
            code = process.wait()
        except BaseException:
            terminate_validation_process_tree(process, owner=owner)
            raise
    _, remaining = psutil.wait_procs(list(known.values()), timeout=2)
    residual: list[JsonValue] = []
    for child in remaining:
        with suppress(psutil.NoSuchProcess):
            residual.append({"pid": child.pid, "create_time": child.create_time()})
    for child in remaining:
        with suppress(psutil.NoSuchProcess):
            child.terminate()
    _, remaining = psutil.wait_procs(remaining, timeout=3)
    for child in remaining:
        with suppress(psutil.NoSuchProcess):
            child.kill()
    _, remaining = psutil.wait_procs(remaining, timeout=3)
    result: dict[str, JsonValue] = {
        "status": "passed"
        if code == 0 and not timed_out and not residual
        else "failed",
        "returncode": code,
        "watchdog_timeout": timed_out,
        "wall_seconds": time.monotonic() - started,
        "residual_before_cleanup": residual,
        "survivors": [child.pid for child in remaining],
    }
    write_json(directory / "outcome.json", result)
    return result


def _failure(path: Path, line: int) -> dict[str, JsonValue]:
    return {
        "phase": "incomplete_log",
        "status": "failed",
        "path": str(path),
        "line": line,
    }


def read_events(path: Path) -> list[dict[str, JsonValue]]:
    """Keep malformed and partially written records as failures, never zero timings."""
    rows: list[dict[str, JsonValue]] = []
    if path.is_symlink():
        return [_failure(path, 0)]
    for number, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
    ):
        try:
            row = TypeAdapter(dict[str, JsonValue]).validate_json(line)
            if not isinstance(row.get("phase"), str) or not row["phase"]:
                raise ValueError("missing phase")
            for field in ("seconds", "clock_ns", "monotonic_ns"):
                value = row.get(field)
                if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise ValueError("invalid time")
        except ValueError:
            row = _failure(path, number)
        rows.append(row)
    return rows


def _case_directory(root: Path, name: str) -> Path:
    if not name or name in {".", ".."} or any(char in name for char in "/\\:"):
        raise ValueError("case names must be single directory names")
    return root / name


def summarize(
    root: Path, cases: list[dict[str, JsonValue]], metadata: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    timings: dict[str, list[float]] = {}
    for case in cases:
        name = case.get("name")
        if not isinstance(name, str):
            raise ValueError("case requires a name")
        directory = _case_directory(root, name)
        if directory.is_symlink():
            case["status"] = "failed"
            case["incomplete_logs"] = [_failure(directory, 0)]
            continue
        events = directory / "events.jsonl"
        rows = read_events(events) if events.exists() else []
        case["events"] = cast("list[JsonValue]", rows)
        timing = directory / "timing"
        nested = (
            [_failure(timing, 0)]
            if timing.is_symlink()
            else [
                row
                for path in sorted(timing.glob("*.jsonl"))
                if path.is_file()
                for row in read_events(path)
            ]
        )
        incomplete = [
            row for row in [*rows, *nested] if row.get("phase") == "incomplete_log"
        ]
        if incomplete:
            case["incomplete_logs"] = cast("list[JsonValue]", incomplete)
        if incomplete or any(row.get("status") == "failed" for row in rows):
            case["status"] = "failed"
        run_id = next(
            (row.get("run_id") for row in rows if row.get("phase") == "run"), None
        )
        submit = next(
            (
                row.get("clock_ns")
                for row in rows
                if row.get("phase") == "submit" and row.get("status") == "started"
            ),
            None,
        )
        first = next(
            (
                row.get("monotonic_ns")
                for row in nested
                if isinstance(run_id, str)
                and row.get("run_id") == run_id
                and row.get("phase") == "first_measurement_ingested"
            ),
            None,
        )
        if (
            isinstance(submit, (int, float))
            and isinstance(first, (int, float))
            and first >= submit
        ):
            latency = (first - submit) / 1e9
            case["submit_to_first_ingest_seconds"] = latency
            timings.setdefault("submit_to_first_ingest", []).append(latency)
        for row in rows:
            seconds = row.get("seconds")
            if (
                row.get("status") == "passed"
                and isinstance(seconds, (int, float))
                and not isinstance(seconds, bool)
            ):
                timings.setdefault(str(row["phase"]), []).append(float(seconds))
    result: dict[str, JsonValue] = {
        "format": 1,
        "metadata": metadata,
        "cases": cast("list[JsonValue]", cases),
        "timings_seconds": cast(
            "dict[str, JsonValue]",
            {
                name: {
                    "samples": cast("list[JsonValue]", values),
                    "median": statistics.median(values),
                    "min": min(values),
                    "max": max(values),
                }
                for name, values in timings.items()
            },
        ),
        "passed": len(cases) == metadata["expected_cases"]
        and all(case.get("status") == "passed" for case in cases),
    }
    write_json(root / "summary.json", result)
    lines = [
        "# Host diagnostic report",
        "",
        "Overall: " + ("passed" if result["passed"] else "failed or incomplete"),
        "",
        (
            "This report describes only the requested software workload, not "
            "physical-device or human usability qualification."
        ),
        "OS caches are not cleared. A new process is not evidence of a cold OS cache.",
        (
            "The watchdog bounds the diagnostic command; it does not change "
            "framework timeouts. Later success does not erase earlier failure."
        ),
        "",
        "| Case | Status | Wall seconds |",
        "|---|---|---:|",
    ]
    for case in cases:
        lines.append(
            f"| {case['name']} | {case['status']} | "
            f"{case.get('wall_seconds', 'unavailable')} |"
        )
    lines += [
        "",
        "| Phase | Samples | Median seconds | Min-max |",
        "|---|---:|---:|---:|",
    ]
    for name, values in timings.items():
        lines.append(
            f"| {name} | {len(values)} | {statistics.median(values):.3f} | "
            f"{min(values):.3f}-{max(values):.3f} |"
        )
    lines += [
        "",
        (
            "Inspect worker.log, events.jsonl and diagnostics/ for failures. "
            "Unfinished stages are not recorded as zero duration."
        ),
        (
            "wait_result includes execution and completion waiting; it is not "
            "time to first data."
        ),
        (
            "Compare first and later cases separately in summary.json. Project "
            "files remain local; the archive includes only explicitly selected "
            "diagnostic evidence."
        ),
        "No files are uploaded automatically.",
    ]
    report = root / "report.md"
    if report.is_symlink():
        raise ValueError("diagnostic output must not be a symlink")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def archive_diagnostics(root: Path, case_names: Sequence[str]) -> Path:
    """Archive a fixed evidence allowlist, never project trees or symlink targets."""
    if root.is_symlink():
        raise ValueError("diagnostic root must not be a symlink")
    paths = [root / name for name in ("metadata.json", "summary.json", "report.md")]
    for name in dict.fromkeys(case_names):
        directory = _case_directory(root, name)
        if directory.is_symlink():
            continue
        paths.extend(
            directory / name for name in ("worker.log", "outcome.json", "events.jsonl")
        )
        paths.extend(directory.glob("*-kernel.log"))
        for folder, patterns in (
            ("timing", ("*.jsonl",)),
            ("diagnostics", ("*.log", "*.jsonl")),
        ):
            parent = directory / folder
            if not parent.is_symlink():
                for pattern in patterns:
                    paths.extend(parent.glob(pattern))
    target = root.with_name(root.name + ".zip")
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(set(paths)):
            if path.is_file() and not path.is_symlink():
                bundle.write(path, path.relative_to(root))
    return target
