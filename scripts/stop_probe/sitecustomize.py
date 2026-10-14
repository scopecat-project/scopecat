"""Temporary synthetic-test diagnostics; does not change wait/kill semantics."""

import faulthandler
import json
import os
import time
from pathlib import Path

import psutil

directory = Path(os.environ["SCOPECAT_STOP_PROBE"]).resolve()
directory.mkdir(parents=True, exist_ok=True)
stack = (directory / f"stacks-{os.getpid()}.log").open("w", encoding="utf-8")
faulthandler.dump_traceback_later(30, repeat=True, file=stack)
original_wait = psutil.Process.wait
original_kill = psutil.Process.kill


def record(event, process, **extra):
    row = {
        "event": event,
        "observer": os.getpid(),
        "pid": process.pid,
        "clock": time.monotonic(),
        **extra,
    }
    try:
        row["identity"] = process.as_dict(
            attrs=["pid", "ppid", "create_time", "status", "name", "num_threads"]
        )
        row["children"] = [
            p.as_dict(attrs=["pid", "ppid", "create_time", "status", "name"])
            for p in process.children(recursive=True)
        ]
    except psutil.Error as error:
        row["observation_error"] = type(error).__name__
    with (directory / f"process-{os.getpid()}.jsonl").open(
        "a", encoding="utf-8"
    ) as stream:
        stream.write(json.dumps(row) + "\n")


def wait(self, timeout=None):
    record("wait_start", self, timeout=timeout)
    try:
        code = original_wait(self, timeout)
    except BaseException as error:
        record("wait_error", self, error=type(error).__name__)
        raise
    record("wait_end", self, exitcode=code)
    return code


def kill(self):
    record("kill_start", self)
    try:
        result = original_kill(self)
    except BaseException as error:
        record("kill_error", self, error=type(error).__name__)
        raise
    record("kill_returned", self)
    return result


psutil.Process.wait = wait
psutil.Process.kill = kill
