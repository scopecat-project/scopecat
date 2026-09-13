"""Opt-in, same-host diagnostic timestamps; never execution authority."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

TIMING_DIRECTORY_ENV = "SCOPECAT_TIMING_DIRECTORY"


def record_timing(phase: str, **identity: str | int | None) -> None:
    directory = os.environ.get(TIMING_DIRECTORY_ENV)
    if directory is None:
        return
    event = {
        "phase": phase,
        "monotonic_ns": time.monotonic_ns(),
        "pid": os.getpid(),
        **identity,
    }
    # Each process owns its file; callers emit only sparse lifecycle boundaries.
    try:
        with (Path(directory) / f"timing-{os.getpid()}.jsonl").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(json.dumps(event) + "\n")
    except OSError as error:
        # Diagnostics must not change a possibly committed operation's outcome.
        logging.getLogger(__name__).warning("Interaction timing unavailable: %s", error)
