"""Synthetic end-to-end authoring, execution, recovery, analysis and reader.

No devices are discovered or contacted. The provider audit is deliberately not a
recovery oracle: a lost return remains unknown until the operator permits retry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .authoring import Axis, Intent
from .core import _SCHEMA, Store
from .execution import drain

BOUNDARIES = (
    "provider-return",
    "result-save",
    "group-publish",
    "notification",
    "adaptive-append",
    "adaptive-close",
)


class InjectedCrash(RuntimeError):
    """Terminate this command at a durable boundary; do not unwind the ledger."""


def encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def declaration() -> Intent:
    """One author intent; point IDs, memberships and traversal are derived."""
    return Intent(
        run_id="synthetic-2d",
        axes=(
            Axis("frequency", (5.0, 5.1), "GHz"),
            Axis("bias", (0.0, 0.2), "V", labels=("low", "high")),
        ),
        work_by=("bias",),
        group_by=("bias",),
        static_closed=True,
        partition_identity="axis-index",
        selection_policy="sole_candidate",
        reverse_axes=("bias",),
        open_groups=("adaptive",),
        outputs={
            "waveform": {
                "dims": ["point", "channel", "sample"],
                "shape": [None, 2, 16],
                "dtype": "float64",
                "unit": "V",
                "coordinates": {
                    "channel": {"values": ["I", "Q"], "unit": ""},
                    "sample": {"values": list(range(16)), "unit": "ns"},
                },
            },
            "diagnostic": {
                "dims": ["point"],
                "shape": [None],
                "dtype": "float64",
                "unit": "ratio",
                "coordinates": {},
            },
        },
    )


def connect_aux(path: Path) -> sqlite3.Connection:
    # Match ordinary SQLite full durability; do not trade persistence for speed.
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def simulate_provider(
    directory: Path, attempt_id: str, unit: dict, points: dict
) -> list[dict]:
    """One command returns a two-channel block plus a separate diagnostic block."""
    with connect_aux(directory / "provider.sqlite3") as audit:
        audit.execute(
            "CREATE TABLE IF NOT EXISTS calls "
            "(attempt_id TEXT PRIMARY KEY, unit_id TEXT NOT NULL)"
        )
        audit.execute("INSERT INTO calls VALUES (?, ?)", (attempt_id, unit["id"]))
    ids = unit["point_ids"]
    samples = 16
    values: list[float | None] = []
    available: list[bool] = []
    for identity in ids:
        coordinates = points[identity]["coordinates"]
        frequency = coordinates["frequency"]["value"]
        bias = coordinates["bias"]["value"]
        for channel in range(2):
            for sample in range(samples):
                valid = not (
                    frequency == 5.1 and bias == 0.2 and channel == 1 and sample >= 12
                )
                available.append(valid)
                values.append(
                    round(bias + (frequency - 4.9) * math.sin(sample / 3 + channel), 8)
                    if valid
                    else None
                )
    provenance = {
        "provider": "synthetic-only",
        "command_id": attempt_id,
        "synchronization": "shared simulated command; no hardware timing claim",
    }
    return [
        {
            "id": "waveform",
            "point_ids": ids,
            "dims": ["point", "channel", "sample"],
            "shape": [len(ids), 2, samples],
            "dtype": "float64",
            "unit": "V",
            "values": values,
            "availability": available,
            "coordinates": {
                "channel": {"values": ["I", "Q"], "unit": ""},
                "sample": {"values": list(range(samples)), "unit": "ns"},
            },
            "provenance": provenance,
        },
        {
            "id": "diagnostic",
            "point_ids": ids,
            "dims": ["point"],
            "shape": [len(ids)],
            "dtype": "float64",
            "unit": "ratio",
            "values": [1.0 for _ in ids],
            "availability": [True for _ in ids],
            "coordinates": {},
            "provenance": provenance,
        },
    ]


def analyze(snapshot: dict) -> dict:
    """Consume only the frozen snapshot, never a latest-result query."""
    rows = []
    for bundle in snapshot["bundles"]:
        for output in bundle["outputs"]:
            if output["id"] != "waveform":
                continue
            # The demo analysis declares its own supported tensor contract.
            if output["dims"] != ["point", "channel", "sample"]:
                raise ValueError("demo mean analysis requires point/channel/sample")
            count, channels, samples = output["shape"]
            for p in range(count):
                for channel in range(channels):
                    start = (p * channels + channel) * samples
                    selected = [
                        value
                        for value, valid in zip(
                            output["values"][start : start + samples],
                            output["availability"][start : start + samples],
                            strict=True,
                        )
                        if valid
                    ]
                    rows.append(
                        {
                            "point_id": output["point_ids"][p],
                            "channel": output["coordinates"]["channel"]["values"][
                                channel
                            ],
                            "mean": sum(selected) / len(selected) if selected else None,
                            "available_samples": len(selected),
                            "expected_samples": samples,
                            "unit": output["unit"],
                            "bundle_hash": bundle["hash"],
                        }
                    )
    return {
        "analysis": "available-sample-mean/v1",
        "snapshot_id": snapshot["id"],
        "input_sha256": hashlib.sha256(encode(snapshot).encode()).hexdigest(),
        "missing_samples_policy": "exclude explicitly unavailable; retain counts",
        "rows": rows,
    }


def write_analysis(directory: Path, snapshot: dict) -> dict:
    result = analyze(snapshot)
    destination = (
        directory / "analyses" / (snapshot["id"].removeprefix("sha256:") + ".json")
    )
    destination.parent.mkdir(exist_ok=True)
    content = encode(result) + "\n"
    # Fully write and sync a private staging file, then atomically publish without
    # clobbering an existing result. A killed writer cannot leave a partial final.
    staging: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=destination.parent, prefix=".analysis-", delete=False
        ) as stream:
            staging = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(staging, destination)
        except FileExistsError:
            if destination.read_text() != content:
                raise ValueError(
                    "analysis identity already has different content"
                ) from None
        if hasattr(os, "O_DIRECTORY"):
            descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)
    return result


def notify(
    directory: Path,
    store: Store,
    crash_at: str | None = None,
    *,
    hard_crash: bool = False,
) -> None:
    """At-least-once consumer, durable cursor and per-event idempotent effects."""
    with connect_aux(directory / "notifications.sqlite3") as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS cursor (
                id INTEGER PRIMARY KEY CHECK(id=1), seq INTEGER NOT NULL);
            INSERT OR IGNORE INTO cursor VALUES (1, 0);
            CREATE TABLE IF NOT EXISTS deliveries (
                seq INTEGER PRIMARY KEY, snapshot_id TEXT NOT NULL,
                delivery_attempts INTEGER NOT NULL);
        """)
        cursor = connection.execute("SELECT seq FROM cursor WHERE id=1").fetchone()[0]
        for event in store.events(after=cursor):
            if event["kind"] == "group_published":
                snapshot_id = event["payload"]["snapshot_id"]
                write_analysis(directory, store.snapshot(snapshot_id))
                connection.execute(
                    """INSERT INTO deliveries VALUES (?, ?, 1)
                    ON CONFLICT(seq) DO UPDATE SET
                        delivery_attempts=delivery_attempts+1""",
                    (event["seq"], snapshot_id),
                )
                connection.commit()
                crash("notification", crash_at, hard_crash=hard_crash)
            connection.execute("UPDATE cursor SET seq=? WHERE id=1", (event["seq"],))
            connection.commit()


def crash(boundary: str, crash_at: str | None, *, hard_crash: bool = False) -> None:
    if boundary == crash_at:
        if hard_crash:
            print(
                encode(
                    {
                        "status": "injected-crash",
                        "boundary": boundary,
                        "mode": "process-exit",
                    }
                ),
                flush=True,
            )
            # Deliberately bypass context-manager cleanup, finally blocks and
            # Python shutdown: recovery must use committed on-disk evidence.
            os._exit(75)
        raise InjectedCrash(boundary)


def execute(
    directory: Path,
    *,
    recover: bool,
    authorize_retry: bool = False,
    crash_at: str | None = None,
    hard_crash: bool = False,
    commit_mode: str = "atomic",
) -> dict:
    start = time.perf_counter()
    intent = declaration()
    if recover:
        _preflight_recovery(directory / "ledger.sqlite3", intent)
    with Store(directory / "ledger.sqlite3") as store:
        if not recover:
            intent.initialize(store, commit_mode=commit_mode)
        else:
            _verify_recovery_identity(store, intent)
        fence = store.acquire("synthetic-cli")
        if recover:
            store.reconcile_unknowns(fence)
            unknown = [
                attempt
                for attempt in store.state()["attempts"]
                if attempt["status"] == "unknown"
            ]
            if unknown and not authorize_retry:
                return {
                    "status": "blocked",
                    "reason": (
                        "physical result unknown; explicit --authorize-retry required"
                    ),
                    "unknown_attempt_ids": [item["id"] for item in unknown],
                }
            for attempt in unknown:
                store.authorize_retry(
                    fence,
                    attempt["id"],
                    "operator explicitly authorized synthetic retry via CLI",
                )
        # Recover already-published notifications before doing new work.
        notify(directory, store)
        first_readable = None

        def fault(boundary: str) -> None:
            crash(boundary, crash_at, hard_crash=hard_crash)

        def published(_group_id: str, _snapshot_id: str) -> None:
            nonlocal first_readable
            if first_readable is None:
                first_readable = time.perf_counter() - start
            notify(directory, store, crash_at, hard_crash=hard_crash)

        def provider(attempt_id: str, unit: dict, points: dict) -> list[dict]:
            return simulate_provider(directory, attempt_id, unit, points)

        # Generic runtime interprets persisted traversal and selection policy.
        drain(store, fence, provider, fault=fault, on_publish=published)
        state = store.state()
        adaptive = next(group for group in state["groups"] if group["id"] == "adaptive")
        if not adaptive["unit_ids"]:
            high = next(group for group in state["groups"] if group["id"] == "high")
            snapshot = store.snapshot(high["published_snapshot"])
            source = analyze(snapshot)
            measured = next(
                row["mean"] for row in source["rows"] if row["mean"] is not None
            )
            intent.extend(
                store,
                fence,
                group_id="adaptive",
                proposal_id="refine-1",
                axis_values={
                    "frequency": (5.2, 5.3),
                    "bias": (round(float(measured), 6),),
                },
                source_snapshot=snapshot["id"],
                rule_id="first-available-channel-mean-rounded-6/v1",
            )
            fault("adaptive-append")
        if not adaptive["closed"]:
            store.close_group(fence, "adaptive")
            fault("adaptive-close")
        drain(store, fence, provider, fault=fault, on_publish=published)
        result = inspect(directory, store=store)
        result["status"] = "completed"
        result["invocation_seconds"] = time.perf_counter() - start
        result["first_sparse_group_seconds"] = first_readable
        return result


def _recovery_schema(connection: sqlite3.Connection) -> list[tuple]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
            "ORDER BY type,name"
        )
    ]


def _verify_recovery_identity(store: Store, intent: Intent) -> None:
    """Require this exact experiment format and admitted author declaration."""
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript(_SCHEMA)
        if _recovery_schema(store.connection) != _recovery_schema(reference):
            raise ValueError("experiment ledger schema does not match this version")
    finally:
        reference.close()
    rows = store.connection.execute("SELECT id,initial_intent FROM run").fetchall()
    if len(rows) != 1:
        raise ValueError("experiment ledger must contain one admitted run")
    admitted = json.loads(rows[0]["initial_intent"])
    if not isinstance(admitted, dict):
        raise ValueError("experiment ledger has an invalid admitted intent")
    authored = admitted.get("author_intent")
    if not isinstance(authored, dict):
        raise ValueError("experiment ledger lacks its author identity")
    expected = intent.compile(commit_mode=authored.get("commit_mode"))
    if rows[0]["id"] != intent.run_id or admitted != expected:
        raise ValueError("experiment ledger author identity does not match this demo")
    intent.verify(store)


def _preflight_recovery(path: Path, intent: Intent) -> None:
    """Read source files without SQLite opening or changing an unverified ledger.

    SQLite mode=ro can still create WAL shared-memory sidecars. A private copy
    lets the read-only inspection include a committed crash WAL while leaving
    rejected source databases, journal policy and directory contents untouched.
    Recovery assumes no concurrently running producer, as does the simulator's
    explicit fence takeover; identity is checked again before taking that fence.
    """
    try:
        if not path.is_file():
            raise ValueError("experiment ledger does not exist")
        with tempfile.TemporaryDirectory(prefix="result-ledger-preflight-") as tmp:
            copied = Path(tmp) / "ledger.sqlite3"
            shutil.copyfile(path, copied)
            wal = path.with_name(path.name + "-wal")
            if wal.is_file():
                shutil.copyfile(wal, copied.with_name(copied.name + "-wal"))
            with Store(copied, readonly=True) as reader:
                _verify_recovery_identity(reader, intent)
    except (
        OSError,
        sqlite3.Error,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
    ) as error:
        raise ValueError(
            f"refusing recovery: not a matching result-ledger experiment ({error})"
        ) from error


def inspect(directory: Path, *, store: Store | None = None) -> dict:
    if store is None:
        with Store(directory / "ledger.sqlite3", readonly=True) as reader:
            return inspect(directory, store=reader)
    state = store.state()
    state["analyses"] = [
        json.loads(path.read_text())
        for path in sorted((directory / "analyses").glob("*.json"))
    ]
    state["deliveries"] = []
    state["notification_cursor"] = 0
    if (directory / "notifications.sqlite3").exists():
        with sqlite3.connect(
            (directory / "notifications.sqlite3").as_uri() + "?mode=ro", uri=True
        ) as connection:
            connection.row_factory = sqlite3.Row
            state["deliveries"] = [
                dict(row)
                for row in connection.execute("SELECT * FROM deliveries ORDER BY seq")
            ]
            state["notification_cursor"] = connection.execute(
                "SELECT seq FROM cursor WHERE id=1"
            ).fetchone()[0]
    state["provider_calls"] = []
    if (directory / "provider.sqlite3").exists():
        with sqlite3.connect(
            (directory / "provider.sqlite3").as_uri() + "?mode=ro", uri=True
        ) as connection:
            connection.row_factory = sqlite3.Row
            state["provider_calls"] = [
                dict(row)
                for row in connection.execute("SELECT * FROM calls ORDER BY rowid")
            ]
    state["counts"] = {
        key: len(state[key])
        for key in ("points", "units", "attempts", "results", "selections", "snapshots")
    }
    return state


def serve(directory: Path, port: int) -> None:
    viewer = Path(__file__).with_name("viewer.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                content, mime = viewer, "text/html; charset=utf-8"
            elif path == "/api/state":
                content, mime = encode(inspect(directory)).encode(), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(
        f"Synthetic experiment reader: http://127.0.0.1:{server.server_port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "benchmark":
        from .benchmark import main as benchmark_main

        return benchmark_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("demo", "recover", "inspect", "serve"):
        selected = subparsers.add_parser(command)
        selected.add_argument("--directory", type=Path, required=True)
        if command == "demo":
            selected.add_argument("--crash-at", choices=BOUNDARIES)
            selected.add_argument(
                "--commit-mode",
                choices=("atomic", "staged"),
                default="atomic",
                help="persist result/selection/publication atomically or in stages",
            )
            selected.add_argument(
                "--hard-crash",
                action="store_true",
                help="exit the process without cleanup at --crash-at",
            )
        if command == "recover":
            selected.add_argument("--authorize-retry", action="store_true")
        if command == "serve":
            selected.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if getattr(args, "hard_crash", False) and not args.crash_at:
        parser.error("--hard-crash requires --crash-at")
    directory = args.directory.resolve()
    if args.command == "demo":
        if directory.exists():
            parser.error(
                "demo requires a new directory; existing data is never replaced"
            )
        directory.mkdir(parents=True)
    elif not (directory / "ledger.sqlite3").is_file():
        parser.error("directory does not contain an experiment ledger")
    try:
        if args.command == "serve":
            serve(directory, args.port)
            return 0
        if args.command == "inspect":
            result = inspect(directory)
        else:
            result = execute(
                directory,
                recover=args.command == "recover",
                authorize_retry=getattr(args, "authorize_retry", False),
                crash_at=getattr(args, "crash_at", None),
                hard_crash=getattr(args, "hard_crash", False),
                commit_mode=getattr(args, "commit_mode", "atomic"),
            )
        print(json.dumps(result, indent=2, allow_nan=False))
        return 3 if result.get("status") == "blocked" else 0
    except InjectedCrash as error:
        print(
            encode(
                {
                    "status": "injected-crash",
                    "boundary": str(error),
                    "directory": str(directory),
                }
            )
        )
        return 75
    except (ValueError, RuntimeError) as error:
        print(encode({"status": "error", "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
