"""Project bounded existing pytest evidence; never infer a worker crash cause."""

from __future__ import annotations

import html
import json
import os
import re
import sys
from itertools import islice
from pathlib import Path
from typing import cast

_MAX_FILES = 32
_TAIL_BYTES = 65536
_WORKER = re.compile(r"(controller|gw[0-9]+)-([0-9]+)\.log")
_DAEMON = re.compile(r"daemon-lifetime-[0-9]+-[0-9]+-[0-9]+\.jsonl")
_EVENT = re.compile(
    r"^\S+ ((?:setup|call|teardown) (?:start|end) .+|session finish [0-9]+)$"
)


def _tail(path: Path) -> str:
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - _TAIL_BYTES))
        data = stream.read(_TAIL_BYTES)
    if size > _TAIL_BYTES:
        data = data.partition(b"\n")[2]
    return data.decode("utf-8", errors="replace")


def _safe(value: str) -> str:
    return html.escape("".join(c if c.isprintable() else " " for c in value)[:512])


def _worker_row(path: Path, worker: str, pid: str) -> str:
    phase = "unknown (no phase in retained tail)"
    status = "unknown (no session finish in retained tail)"
    for line in _tail(path).splitlines():
        match = _EVENT.fullmatch(line)
        if match is None:
            continue
        event = match[1]
        if event.startswith("session finish "):
            status = event.removeprefix("session finish ")
        else:
            phase = event
    return f"{worker} PID {pid}: pytest session status={status}; last phase={phase}"


def _daemon_row(path: Path) -> str:
    # Only scalar identity/exit fields are projected, never daemon logs or paths.
    for line in reversed(_tail(path).splitlines()):
        try:
            row = cast("object", json.loads(line))
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        fields = cast("dict[str, object]", row)
        pid = fields.get("pid")
        observer = fields.get("observer_pid")
        worker = fields.get("worker")
        code = fields.get("returncode")
        if type(pid) is not int or type(observer) is not int:
            continue
        worker = (
            worker
            if isinstance(worker, str) and re.fullmatch(r"gw[0-9]+", worker)
            else "controller/unknown"
        )
        code_text = str(code) if type(code) is int else "unknown (not observed exited)"
        return (
            f"daemon PID {pid}, observer {worker} PID {observer}: "
            f"returncode={code_text}"
        )
    return "no valid daemon identity/exit record in retained tail"


def summarize(directory: Path) -> str:
    lines = [
        "### Existing pytest diagnostics",
        "",
        (
            "Bounded projection: scan at most 256 entries, show at most 32 files, "
            "last 64 KiB per file, 512 characters per record. "
            "Pytest session status is not an OS worker exit code; "
            "daemon returncode is a separate process. "
            "Missing records do not establish a crash cause. "
            "Full evidence remains in the artifact."
        ),
        "",
    ]
    try:
        # Cap traversal too: directory ordering is intentionally not chronological.
        entries = list(islice(directory.iterdir(), 257))
    except OSError as error:
        lines.append(f"- Diagnostics unavailable ({type(error).__name__}).")
        return "\n".join(lines) + "\n"
    candidates = sorted(
        (
            path
            for path in entries[:256]
            if _WORKER.fullmatch(path.name) or _DAEMON.fullmatch(path.name)
        ),
        key=lambda path: (_WORKER.fullmatch(path.name) is None, path.name),
    )
    found = False
    for path in candidates[:_MAX_FILES]:
        match = _WORKER.fullmatch(path.name)
        if match is None and _DAEMON.fullmatch(path.name) is None:
            continue
        found = True
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError("not a regular diagnostic file")
            row = _worker_row(path, match[1], match[2]) if match else _daemon_row(path)
        except (OSError, ValueError) as error:
            row = f"unavailable ({type(error).__name__})"
        lines.append(f"- <code>{_safe(path.name)}: {_safe(row)}</code>")
    if not found:
        lines.append("- No matching diagnostic records found.")
    if len(entries) > 256 or len(candidates) > _MAX_FILES:
        lines.append("- File limit reached; additional entries omitted. See artifact.")
    return "\n".join(lines) + "\n"


def main() -> None:
    summary = summarize(Path(sys.argv[1]))
    print(summary, end="")
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with Path(destination).open("a", encoding="utf-8") as stream:
            stream.write(summary)


if __name__ == "__main__":
    main()
