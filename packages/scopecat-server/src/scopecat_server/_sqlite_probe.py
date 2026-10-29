"""Temporary opt-in diagnostic branch probe; not a production fix."""
from __future__ import annotations
import json
import os
import sqlite3
import sys
import threading
import time
import traceback
from pathlib import Path


def emit(event, **fields):
    directory = os.environ.get("SCOPECAT_SQLITE_PROBE")
    if not directory:
        return
    try:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        payload = dict(event=event, clock_ns=time.monotonic_ns(), pid=os.getpid(),
                       thread=threading.get_ident(), **fields)
        with (target / f"sqlite-{os.getpid()}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload) + "\n")
    except OSError:
        pass


class ObservedRLock:
    def __init__(self):
        self.lock = threading.RLock()
        self.owner = None
        self.depth = 0

    def acquire(self, *args, **kwargs):
        started = time.monotonic_ns()
        acquired = self.lock.acquire(*args, **kwargs)
        ended = time.monotonic_ns()
        if acquired:
            if self.depth == 0:
                self.owner = (threading.get_ident(), ended)
            self.depth += 1
        else:
            owner = self.owner
            frame = sys._current_frames().get(owner[0]) if owner else None
            emit("writer_timeout", wait_start_ns=started, wait_end_ns=ended,
                 owner=owner, owner_stack=traceback.format_stack(frame) if frame else [],
                 requester_stack=traceback.format_stack())
        return acquired

    def release(self):
        owner = self.owner
        self.depth -= 1
        outermost = self.depth == 0
        if outermost:
            self.owner = None
        ended = time.monotonic_ns()
        self.lock.release()
        if outermost and owner and ended - owner[1] > 500_000_000:
            emit("slow_writer_release", owner=owner, release_ns=ended,
                 release_stack=traceback.format_stack())

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


def checkpoint(connection, path):
    def sizes():
        result = {}
        for suffix in ("", "-wal", "-shm"):
            try:
                result[suffix or "db"] = Path(str(path) + suffix).stat().st_size
            except OSError:
                result[suffix or "db"] = None
        return result
    emit("checkpoint_start", sizes=sizes(), sqlite=sqlite3.sqlite_version,
         compile_options=[row[0] for row in connection.execute("PRAGMA compile_options")])
    started = time.monotonic_ns()
    try:
        result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        ended = time.monotonic_ns()
        emit("checkpoint_end", start_ns=started, end_ns=ended, result=list(result), sizes=sizes())
        return result
    except BaseException as error:
        emit("checkpoint_error", start_ns=started, error=repr(error))
        raise


def sql_busy(error):
    emit("sqlite_busy", code=error.sqlite_errorcode,
         name=error.sqlite_errorname, stack=traceback.format_exception(error))


def close_writer(writer):
    emit("writer_close_start")
    started = time.monotonic_ns()
    try:
        writer.close()
    finally:
        emit("writer_close_end", start_ns=started, end_ns=time.monotonic_ns())
