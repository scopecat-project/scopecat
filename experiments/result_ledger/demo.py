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
import sqlite3
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .core import Store

BOUNDARIES = ("provider-return", "result-save", "group-publish", "notification")


class InjectedCrash(RuntimeError):
    """Terminate this command at a durable boundary; do not unwind the ledger."""


def encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def point(identity: str, frequency: float, bias: float) -> dict:
    return {
        "id": identity,
        "coordinates": {
            "frequency": {"value": frequency, "unit": "GHz"},
            "bias": {"value": bias, "unit": "V"},
        },
    }


def unit(identity: str, point_ids: list[str]) -> dict:
    return {
        "id": identity,
        "point_ids": point_ids,
        "required_outputs": ["waveform", "diagnostic"],
        "output_contracts": {
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
    }


def declaration() -> dict:
    """A small 2D domain; a work unit intentionally spans nonadjacent points."""
    return {
        "run_id": "synthetic-2d",
        "points": [
            point("p00", 5.0, 0.0),
            point("p01", 5.0, 0.2),
            point("p10", 5.1, 0.0),
            point("p11", 5.1, 0.2),
        ],
        "units": [
            unit("bias-low", ["p00", "p10"]),
            unit("bias-high", ["p01", "p11"]),
        ],
        "groups": [
            {"id": "low", "unit_ids": ["bias-low"], "closed": True},
            {"id": "high", "unit_ids": ["bias-high"], "closed": True},
            {"id": "adaptive", "unit_ids": [], "closed": False},
        ],
    }


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
                valid = not (identity == "p11" and channel == 1 and sample >= 12)
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
) -> dict:
    start = time.perf_counter()
    with Store(directory / "ledger.sqlite3") as store:
        if not recover:
            store.initialize(**declaration())
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
        for unit_id, group_id in (
            ("bias-high", "high"),
            ("bias-low", "low"),
            ("adaptive-2", "adaptive"),
        ):
            state = store.state()
            if unit_id == "adaptive-2" and not any(
                unit["id"] == unit_id for unit in state["units"]
            ):
                # A deterministic observation-derived proposal is persisted once.
                high = next(group for group in state["groups"] if group["id"] == "high")
                source = analyze(store.snapshot(high["published_snapshot"]))
                measured = next(
                    row["mean"] for row in source["rows"] if row["mean"] is not None
                )
                bias = round(float(measured), 6)
                store.append_work(
                    fence,
                    [point("p20", 5.2, bias), point("p21", 5.3, bias)],
                    [unit(unit_id, ["p20", "p21"])],
                    "adaptive",
                )
                state = store.state()
            group = next(group for group in state["groups"] if group["id"] == group_id)
            if not group["closed"]:
                store.close_group(fence, group_id)
            if group["published_snapshot"]:
                continue
            selected = next(
                (item for item in state["selections"] if item["unit_id"] == unit_id),
                None,
            )
            if selected is None:
                saved = [
                    item for item in state["results"] if item["unit_id"] == unit_id
                ]
                if len(saved) > 1:
                    raise ValueError(
                        "multiple saved candidates require explicit selection; "
                        "demo refuses guessing"
                    )
                if saved:
                    bundle_hash = saved[0]["hash"]
                else:
                    current_unit = next(
                        unit for unit in state["units"] if unit["id"] == unit_id
                    )
                    attempt_id = store.begin_attempt(fence, unit_id)
                    outputs = simulate_provider(
                        directory,
                        attempt_id,
                        current_unit,
                        {point["id"]: point for point in state["points"]},
                    )
                    crash("provider-return", crash_at, hard_crash=hard_crash)
                    bundle_hash = store.save_result(
                        fence,
                        attempt_id,
                        outputs,
                        {"provider": "synthetic-only", "attempt_id": attempt_id},
                    )
                    crash("result-save", crash_at, hard_crash=hard_crash)
                # Explicit demo policy: select the sole persisted completed candidate.
                store.select_result(fence, unit_id, bundle_hash)
            store.publish_group(fence, group_id)
            if first_readable is None:
                first_readable = time.perf_counter() - start
            crash("group-publish", crash_at, hard_crash=hard_crash)
            notify(directory, store, crash_at, hard_crash=hard_crash)
        result = inspect(directory, store=store)
        result["status"] = "completed"
        result["invocation_seconds"] = time.perf_counter() - start
        result["first_sparse_group_seconds"] = first_readable
        return result


def inspect(directory: Path, *, store: Store | None = None) -> dict:
    if store is None:
        with Store(directory / "ledger.sqlite3", readonly=True) as reader:
            return inspect(directory, store=reader)
    state = store.state()
    selected_units = {selection["unit_id"] for selection in state["selections"]}
    # Derive readiness from this same reader snapshot, not later transactions.
    state["readiness"] = {}
    for group in state["groups"]:
        missing = [
            identity for identity in group["unit_ids"] if identity not in selected_units
        ]
        state["readiness"][group["id"]] = {
            "closed": group["closed"],
            "ready": group["closed"] and not missing,
            "missing_units": missing,
            "published_snapshot": group["published_snapshot"],
        }
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
