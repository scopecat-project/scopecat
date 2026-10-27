"""A work/result ledger, independent of the production point-record pipeline.

SQLite is the authority, not an event replay engine. The outbox only announces
immutable published inputs. A work unit may cover many scientific points and
produce several array blocks. Physical execution remains outside transactions.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from math import isfinite, prod
from pathlib import Path
from typing import Any, Self
from uuid import uuid4


class Conflict(RuntimeError):
    """The caller's fence or requested transition contradicts durable state."""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return "sha256:" + sha256(_json(value).encode()).hexdigest()


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be nonempty text")
    return value


def _unique(values: Any, label: str, *, nonempty: bool = True) -> list[str]:
    if not isinstance(values, list) or (nonempty and not values):
        raise ValueError(f"{label} must be a {'nonempty ' if nonempty else ''}list")
    for value in values:
        _text(value, label)
    if len(set(values)) != len(values):
        raise ValueError(f"{label} contains duplicate identities")
    return values


def _finite(value: Any) -> None:
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("scientific values must be finite; use explicit availability")
    if isinstance(value, dict):
        for child in value.values():
            _finite(child)
    elif isinstance(value, list):
        for child in value:
            _finite(child)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS run (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), id TEXT NOT NULL,
 initial_intent TEXT NOT NULL, fence INTEGER NOT NULL DEFAULT 0, owner TEXT
);
CREATE TABLE IF NOT EXISTS points (id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS units (id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS groups (
 id TEXT PRIMARY KEY, closed INTEGER NOT NULL, snapshot_id TEXT
);
CREATE TABLE IF NOT EXISTS members (
 group_id TEXT NOT NULL REFERENCES groups(id),
 unit_id TEXT NOT NULL REFERENCES units(id), ordinal INTEGER NOT NULL,
 PRIMARY KEY(group_id,unit_id), UNIQUE(group_id,ordinal)
);
CREATE TABLE IF NOT EXISTS attempts (
 id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES units(id),
 fence INTEGER NOT NULL, status TEXT NOT NULL,
 physical_status TEXT NOT NULL DEFAULT 'unconfirmed'
   CHECK(physical_status IN ('unconfirmed','unknown','result_observed')),
 reason TEXT, bundle_hash TEXT, retry_reason TEXT,
 retry_authorized_by TEXT, retry_authorized_fence INTEGER
);
CREATE TABLE IF NOT EXISTS bundles (
 hash TEXT PRIMARY KEY, attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(id),
 unit_id TEXT NOT NULL REFERENCES units(id), body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS selections (
 unit_id TEXT PRIMARY KEY REFERENCES units(id),
 bundle_hash TEXT NOT NULL REFERENCES bundles(hash)
);
CREATE TABLE IF NOT EXISTS selection_history (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, unit_id TEXT NOT NULL,
 bundle_hash TEXT NOT NULL, fence INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
 id TEXT PRIMARY KEY, group_id TEXT NOT NULL UNIQUE REFERENCES groups(id),
 body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, payload TEXT NOT NULL
);
"""


class Store:
    """One experiment run per database, with durable fenced transactions.

    Uses the baseline's WAL/NORMAL durability policy, without benchmark-specific
    weakening. ``acquire`` is a deliberately
    explicit lease takeover for this single-process simulator, not a production
    lease manager. Its generation fences every subsequent mutation.
    """

    def __init__(self, path: str | Path, *, readonly: bool = False):
        self.path = Path(path)
        self.readonly = readonly
        if readonly:
            self.connection = sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=ro",
                uri=True,
                isolation_level=None,
            )
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        if not readonly:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=NORMAL")
            self.connection.executescript(_SCHEMA)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def _write(self, fence: int | None = None) -> Iterator[None]:
        if self.readonly:
            raise Conflict("read-only stores cannot mutate execution state")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            if fence is not None:
                row = self.connection.execute("SELECT fence FROM run").fetchone()
                if row is None or row["fence"] != fence or fence < 1:
                    raise Conflict("stale or absent execution fence")
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    @contextmanager
    def _read(self) -> Iterator[None]:
        if self.connection.in_transaction:
            yield
            return
        self.connection.execute("BEGIN")
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def _get(self, table: str, identity: str) -> sqlite3.Row:
        # Only internal constants are accepted as table names.
        statements = {
            "points": "SELECT * FROM points WHERE id=?",
            "units": "SELECT * FROM units WHERE id=?",
            "groups": "SELECT * FROM groups WHERE id=?",
            "attempts": "SELECT * FROM attempts WHERE id=?",
            "snapshots": "SELECT * FROM snapshots WHERE id=?",
        }
        row = self.connection.execute(statements[table], (identity,)).fetchone()
        if row is None:
            raise Conflict(f"unknown {table} identity: {identity}")
        return row

    def initialize(
        self, run_id: str, points: list[dict], units: list[dict], groups: list[dict]
    ) -> None:
        _text(run_id, "run id")
        intent = _json(
            {"run_id": run_id, "points": points, "units": units, "groups": groups}
        )
        with self._write():
            existing = self.connection.execute("SELECT * FROM run").fetchone()
            if existing is not None:
                if existing["initial_intent"] != intent:
                    raise Conflict("run already has a different initial intent")
                return
            self.connection.execute(
                "INSERT INTO run(singleton,id,initial_intent) VALUES(1,?,?)",
                (run_id, intent),
            )
            self._add_work(points, units)
            group_ids: set[str] = set()
            for group in groups:
                identity = _text(group["id"], "group id")
                if identity in group_ids:
                    raise ValueError("duplicate group id")
                group_ids.add(identity)
                members = _unique(group["unit_ids"], "group unit ids", nonempty=False)
                if not isinstance(group["closed"], bool):
                    raise ValueError("group closed must be boolean")
                self.connection.execute(
                    "INSERT INTO groups(id,closed) VALUES(?,?)",
                    (identity, group["closed"]),
                )
                self._add_members(identity, members)

    def _add_work(self, points: list[dict], units: list[dict]) -> None:
        for point in points:
            identity = _text(point["id"], "point id")
            coordinates = point["coordinates"]
            if not isinstance(coordinates, dict):
                raise ValueError("point coordinates must be a mapping")
            for name, quantity in coordinates.items():
                _text(name, "coordinate name")
                if not isinstance(quantity, dict) or "value" not in quantity:
                    raise ValueError("coordinate requires value and unit")
                if not isinstance(quantity.get("unit"), str):
                    raise ValueError(
                        "coordinate unit must be text (empty for unitless)"
                    )
                if quantity["value"] is None:
                    raise ValueError("planned coordinate cannot be unavailable")
            _finite(point)
            if self.connection.execute(
                "SELECT 1 FROM points WHERE id=?", (identity,)
            ).fetchone():
                raise Conflict("point identities are immutable and cannot be re-added")
            self.connection.execute(
                "INSERT INTO points VALUES(?,?)", (identity, _json(point))
            )
        for unit in units:
            identity = _text(unit["id"], "unit id")
            for point_id in _unique(unit["point_ids"], "unit point ids"):
                self._get("points", point_id)
            _unique(unit["required_outputs"], "required outputs")
            _validate_contracts(unit)
            if self.connection.execute(
                "SELECT 1 FROM units WHERE id=?", (identity,)
            ).fetchone():
                raise Conflict("work identities are immutable and cannot be re-added")
            self.connection.execute(
                "INSERT INTO units VALUES(?,?)", (identity, _json(unit))
            )

    def _add_members(self, group_id: str, unit_ids: list[str]) -> None:
        offset = self.connection.execute(
            "SELECT count(*) FROM members WHERE group_id=?", (group_id,)
        ).fetchone()[0]
        for index, identity in enumerate(unit_ids, offset):
            self._get("units", identity)
            self.connection.execute(
                "INSERT INTO members VALUES(?,?,?)", (group_id, identity, index)
            )

    def acquire(self, owner: str) -> int:
        _text(owner, "owner")
        with self._write():
            if self.connection.execute("SELECT 1 FROM run").fetchone() is None:
                raise Conflict("initialize the run before acquiring it")
            self.connection.execute("UPDATE run SET fence=fence+1, owner=?", (owner,))
            return self.connection.execute("SELECT fence FROM run").fetchone()[0]

    def append_work(
        self, fence: int, points: list[dict], units: list[dict], group_id: str
    ) -> None:
        with self._write(fence):
            group = self._get("groups", group_id)
            if group["closed"]:
                raise Conflict("cannot append work to a closed group")
            self._add_work(points, units)
            self._add_members(group_id, [unit["id"] for unit in units])

    def close_group(self, fence: int, group_id: str) -> None:
        with self._write(fence):
            self._get("groups", group_id)
            self.connection.execute(
                "UPDATE groups SET closed=1 WHERE id=?", (group_id,)
            )

    def begin_attempt(self, fence: int, unit_id: str) -> str:
        with self._write(fence):
            self._get("units", unit_id)
            if self.connection.execute(
                "SELECT 1 FROM selections WHERE unit_id=?", (unit_id,)
            ).fetchone():
                raise Conflict("selected work cannot be executed again")
            if self.connection.execute(
                "SELECT 1 FROM attempts WHERE unit_id=? "
                "AND status IN ('running','unknown')",
                (unit_id,),
            ).fetchone():
                raise Conflict(
                    "work has a running or physically unknown attempt; "
                    "reconcile explicitly"
                )
            identity = str(uuid4())
            self.connection.execute(
                "INSERT INTO attempts(id,unit_id,fence,status) VALUES(?,?,?,'running')",
                (identity, unit_id, fence),
            )
            return identity

    def reconcile_unknowns(self, fence: int) -> list[str]:
        with self._write(fence):
            identities = [
                row[0]
                for row in self.connection.execute(
                    "SELECT id FROM attempts WHERE status='running' AND fence<?",
                    (fence,),
                )
            ]
            self.connection.execute(
                "UPDATE attempts SET status='unknown', physical_status='unknown', "
                "reason='executor ownership changed before result commit' "
                "WHERE status='running' AND fence<?",
                (fence,),
            )
            return identities

    def authorize_retry(self, fence: int, attempt_id: str, reason: str) -> None:
        _text(reason, "retry authorization reason")
        with self._write(fence):
            attempt = self._get("attempts", attempt_id)
            if (
                attempt["status"] == "retry_authorized"
                and attempt["retry_reason"] == reason
            ):
                return
            if attempt["status"] != "unknown":
                raise Conflict("only an unknown attempt can be authorized for retry")
            self.connection.execute(
                "UPDATE attempts SET status='retry_authorized',retry_reason=?, "
                "retry_authorized_by=(SELECT owner FROM run), "
                "retry_authorized_fence=? WHERE id=?",
                (reason, fence, attempt_id),
            )

    def save_result(
        self, fence: int, attempt_id: str, outputs: list[dict], provenance: dict
    ) -> str:
        with self._write(fence):
            attempt = self._get("attempts", attempt_id)
            unit = json.loads(self._get("units", attempt["unit_id"])["body"])
            _validate_outputs(unit, outputs)
            if not isinstance(provenance, dict) or not provenance:
                raise ValueError("result provenance must be a nonempty mapping")
            _finite(provenance)
            payload = {
                "attempt_id": attempt_id,
                "unit_id": attempt["unit_id"],
                "outputs": outputs,
                "provenance": provenance,
            }
            digest = _hash(payload)
            if attempt["status"] == "result_saved":
                if attempt["bundle_hash"] != digest:
                    raise Conflict("attempt already has a different immutable result")
                return digest
            if attempt["status"] != "running" or attempt["fence"] != fence:
                raise Conflict("only the current running attempt can save a result")
            self.connection.execute(
                "INSERT INTO bundles VALUES(?,?,?,?)",
                (digest, attempt_id, attempt["unit_id"], _json(payload)),
            )
            self.connection.execute(
                "UPDATE attempts SET status='result_saved', "
                "physical_status='result_observed',bundle_hash=? WHERE id=?",
                (digest, attempt_id),
            )
            return digest

    def select_result(self, fence: int, unit_id: str, bundle_hash: str) -> None:
        with self._write(fence):
            self._get("units", unit_id)
            bundle = self.connection.execute(
                "SELECT * FROM bundles WHERE hash=?", (bundle_hash,)
            ).fetchone()
            if bundle is None or bundle["unit_id"] != unit_id:
                raise Conflict(
                    "selection must reference a saved result of this work unit"
                )
            prior = self.connection.execute(
                "SELECT bundle_hash FROM selections WHERE unit_id=?", (unit_id,)
            ).fetchone()
            if prior is not None and prior[0] == bundle_hash:
                return
            if self.connection.execute(
                "SELECT 1 FROM members m JOIN groups g ON g.id=m.group_id "
                "WHERE m.unit_id=? AND g.snapshot_id IS NOT NULL",
                (unit_id,),
            ).fetchone():
                raise Conflict("published input selections are immutable")
            self.connection.execute(
                "INSERT INTO selections VALUES(?,?) ON CONFLICT(unit_id) "
                "DO UPDATE SET bundle_hash=excluded.bundle_hash",
                (unit_id, bundle_hash),
            )
            self.connection.execute(
                "INSERT INTO selection_history(unit_id,bundle_hash,fence) "
                "VALUES(?,?,?)",
                (unit_id, bundle_hash, fence),
            )

    def readiness(self, group_id: str) -> dict:
        with self._read():
            group = self._get("groups", group_id)
            missing = [
                row[0]
                for row in self.connection.execute(
                    "SELECT m.unit_id FROM members m "
                    "LEFT JOIN selections s ON s.unit_id=m.unit_id "
                    "WHERE m.group_id=? AND s.unit_id IS NULL ORDER BY m.ordinal",
                    (group_id,),
                )
            ]
            return {
                "closed": bool(group["closed"]),
                "ready": bool(group["closed"]) and not missing,
                "missing_units": missing,
                "published_snapshot": group["snapshot_id"],
            }

    def publish_group(self, fence: int, group_id: str) -> str:
        with self._write(fence):
            readiness = self.readiness(group_id)
            if readiness["published_snapshot"] is not None:
                return readiness["published_snapshot"]
            if not readiness["ready"]:
                raise Conflict(
                    "group must be closed with every required result "
                    "explicitly selected"
                )
            rows = self.connection.execute(
                "SELECT u.body, s.bundle_hash, b.body AS bundle FROM members m "
                "JOIN units u ON u.id=m.unit_id JOIN selections s ON s.unit_id=u.id "
                "JOIN bundles b ON b.hash=s.bundle_hash "
                "WHERE m.group_id=? ORDER BY m.ordinal",
                (group_id,),
            ).fetchall()
            units = [json.loads(row["body"]) for row in rows]
            point_ids = list(
                dict.fromkeys(
                    identity for unit in units for identity in unit["point_ids"]
                )
            )
            payload = {
                "run_id": self.connection.execute("SELECT id FROM run").fetchone()[0],
                "group_id": group_id,
                "points": [
                    json.loads(self._get("points", identity)["body"])
                    for identity in point_ids
                ],
                "units": units,
                "selection": [
                    {"unit_id": unit["id"], "bundle_hash": row["bundle_hash"]}
                    for unit, row in zip(units, rows, strict=True)
                ],
            }
            identity = _hash(payload)
            self.connection.execute(
                "INSERT INTO snapshots VALUES(?,?,?)",
                (identity, group_id, _json(payload)),
            )
            self.connection.execute(
                "UPDATE groups SET snapshot_id=? WHERE id=?", (identity, group_id)
            )
            self.connection.execute(
                "INSERT INTO outbox(kind,payload) VALUES('group_published',?)",
                (_json({"group_id": group_id, "snapshot_id": identity}),),
            )
            return identity

    def snapshot(self, snapshot_id: str) -> dict:
        with self._read():
            row = self._get("snapshots", snapshot_id)
            payload = json.loads(row["body"])
            if _hash(payload) != snapshot_id:
                raise Conflict("stored analysis input failed its content hash")
            bundles = []
            for selection in payload["selection"]:
                digest = selection["bundle_hash"]
                row = self.connection.execute(
                    "SELECT body FROM bundles WHERE hash=?", (digest,)
                ).fetchone()
                if row is None:
                    raise Conflict(
                        "immutable analysis input references a missing bundle"
                    )
                bundle = json.loads(row["body"])
                if _hash(bundle) != digest or bundle["unit_id"] != selection["unit_id"]:
                    raise Conflict(
                        "immutable analysis input bundle failed its content hash"
                    )
                bundles.append({"hash": digest, **bundle})
            return {"id": snapshot_id, **payload, "bundles": bundles}

    def events(self, after: int = 0) -> list[dict]:
        if not isinstance(after, int) or after < 0:
            raise ValueError("event cursor must be nonnegative")
        return [
            {
                "seq": row["seq"],
                "kind": row["kind"],
                "payload": json.loads(row["payload"]),
            }
            for row in self.connection.execute(
                "SELECT * FROM outbox WHERE seq>? ORDER BY seq", (after,)
            )
        ]

    def state(self) -> dict:
        # A coherent reader snapshot, including notifications and publication.
        with self._read():
            run = self.connection.execute("SELECT * FROM run").fetchone()
            if run is None:
                return {
                    "run_id": None,
                    "fence": 0,
                    "points": [],
                    "units": [],
                    "groups": [],
                    "attempts": [],
                    "results": [],
                    "selections": [],
                    "snapshots": [],
                    "events": [],
                }
            groups = [
                {
                    "id": row["id"],
                    "closed": bool(row["closed"]),
                    "unit_ids": [
                        member[0]
                        for member in self.connection.execute(
                            "SELECT unit_id FROM members WHERE group_id=? "
                            "ORDER BY ordinal",
                            (row["id"],),
                        )
                    ],
                    "published_snapshot": row["snapshot_id"],
                }
                for row in self.connection.execute(
                    "SELECT * FROM groups ORDER BY rowid"
                )
            ]
            return {
                "run_id": run["id"],
                "fence": run["fence"],
                "points": [
                    json.loads(row[0])
                    for row in self.connection.execute(
                        "SELECT body FROM points ORDER BY rowid"
                    )
                ],
                "units": [
                    json.loads(row[0])
                    for row in self.connection.execute(
                        "SELECT body FROM units ORDER BY rowid"
                    )
                ],
                "groups": groups,
                "attempts": [
                    dict(row)
                    for row in self.connection.execute(
                        "SELECT * FROM attempts ORDER BY rowid"
                    )
                ],
                "results": [
                    {"hash": row["hash"], **json.loads(row["body"])}
                    for row in self.connection.execute(
                        "SELECT * FROM bundles ORDER BY rowid"
                    )
                ],
                "selections": [
                    dict(row)
                    for row in self.connection.execute(
                        "SELECT * FROM selections ORDER BY rowid"
                    )
                ],
                "selection_history": [
                    dict(row)
                    for row in self.connection.execute(
                        "SELECT * FROM selection_history ORDER BY seq"
                    )
                ],
                "snapshots": [
                    self.snapshot(row[0])
                    for row in self.connection.execute(
                        "SELECT id FROM snapshots ORDER BY rowid"
                    )
                ],
                "events": self.events(),
            }


def _validate_outputs(unit: dict, outputs: list[dict]) -> None:
    if not isinstance(outputs, list) or not outputs:
        raise ValueError("a result requires output blocks")
    coverage: dict[str, set[str]] = {}
    allowed_points = set(unit["point_ids"])
    for block in outputs:
        name = _text(block["id"], "output id")
        point_ids = _unique(block["point_ids"], "output point ids")
        if not set(point_ids) <= allowed_points:
            raise ValueError("output includes points outside its work unit")
        seen = coverage.setdefault(name, set())
        if seen.intersection(point_ids):
            raise ValueError("an output cannot cover the same point twice")
        seen.update(point_ids)
        dims = _unique(block["dims"], "output dimensions")
        shape = block["shape"]
        if (
            not isinstance(shape, list)
            or len(shape) != len(dims)
            or any(
                not isinstance(size, int) or isinstance(size, bool) or size < 1
                for size in shape
            )
        ):
            raise ValueError(
                "output shape must provide positive sizes for every dimension"
            )
        if "point" not in dims or shape[dims.index("point")] != len(point_ids):
            raise ValueError(
                "output point axis must match its explicit point identities"
            )
        if not isinstance(block.get("unit"), str):
            raise ValueError("output requires an explicit unit (empty for unitless)")
        _validate_array_values(block, shape)
        _validate_output_contract(unit, name, block, dims, shape)
        coordinates = block.get("coordinates")
        if not isinstance(coordinates, dict):
            raise ValueError("output coordinates must be an explicit mapping")
        for dim, coordinate in coordinates.items():
            if dim not in dims:
                raise ValueError("coordinate refers to an absent array dimension")
            if not isinstance(coordinate, dict) or not isinstance(
                coordinate.get("unit"), str
            ):
                raise ValueError("array coordinates require values and unit")
            coordinate_values = coordinate.get("values")
            if (
                not isinstance(coordinate_values, list)
                or len(coordinate_values) != shape[dims.index(dim)]
            ):
                raise ValueError("array coordinate length differs from dimension")
            if any(value is None for value in coordinate_values):
                raise ValueError("array coordinates cannot be unavailable")
            if dim == "point" and coordinate_values != point_ids:
                raise ValueError(
                    "point coordinate values contradict explicit point identities"
                )
        if not isinstance(block.get("provenance"), dict) or not block["provenance"]:
            raise ValueError("every output requires provenance")
        _finite(block)
        _json(block)
    for name in unit["required_outputs"]:
        if coverage.get(name, set()) != allowed_points:
            raise ValueError(
                f"required output {name!r} does not cover every work point"
            )


def _validate_array_values(block: dict, shape: list[int]) -> None:
    dtype = _text(block["dtype"], "output dtype")
    if dtype not in {
        "float32",
        "float64",
        "int32",
        "int64",
        "complex64",
        "complex128",
        "bool",
        "string",
    }:
        raise ValueError("unsupported scientific dtype")
    values, available = block["values"], block["availability"]
    size = prod(shape)
    if (
        not isinstance(values, list)
        or not isinstance(available, list)
        or len(values) != size
        or len(available) != size
    ):
        raise ValueError("values and availability must cover every array element")
    for value, present in zip(values, available, strict=True):
        if not isinstance(present, bool):
            raise ValueError("availability must be boolean")
        if value is None:
            if present:
                raise ValueError("available data cannot be null")
            continue
        valid = (
            isinstance(value, bool)
            if dtype == "bool"
            else isinstance(value, str)
            if dtype == "string"
            else isinstance(value, int) and not isinstance(value, bool)
            if dtype.startswith("int")
            else isinstance(value, list)
            and len(value) == 2
            and all(
                isinstance(x, (float, int)) and not isinstance(x, bool) for x in value
            )
            if dtype.startswith("complex")
            else isinstance(value, (float, int)) and not isinstance(value, bool)
        )
        if not valid:
            raise ValueError("output value does not match its dtype")
        if dtype.startswith("int"):
            bits = int(dtype.removeprefix("int"))
            if not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
                raise ValueError("integer value is outside its declared dtype")
        elif dtype.startswith(("float", "complex")):
            components = value if dtype.startswith("complex") else [value]
            limit = (
                3.4028234663852886e38
                if dtype in {"float32", "complex64"}
                else 1.7976931348623157e308
            )
            if any(not -limit <= component <= limit for component in components):
                raise ValueError("floating value is outside its declared dtype")


def _validate_contracts(unit: dict) -> None:
    """Explicit output schemas constrain providers; omission is a deferred schema.

    The demo uses explicit contracts. Deferred schemas remain available for
    comparison fixtures, but only promise complete coverage and self-consistent
    typed data, not agreement with an author's expected physical quantity.
    """
    contracts = unit.get("output_contracts")
    if contracts is None:
        return
    if not isinstance(contracts, dict) or set(contracts) != set(
        unit["required_outputs"]
    ):
        raise ValueError("output contracts must exactly cover required output ids")
    for contract in contracts.values():
        dims = _unique(contract["dims"], "contract dimensions")
        shape = contract["shape"]
        if (
            "point" not in dims
            or not isinstance(shape, list)
            or len(shape) != len(dims)
        ):
            raise ValueError("contract requires a point axis and matching shape")
        for dim, size in zip(dims, shape, strict=True):
            if dim == "point":
                if size is not None:
                    raise ValueError(
                        "contract point size must be None for block partitioning"
                    )
            elif not isinstance(size, int) or isinstance(size, bool) or size < 1:
                raise ValueError("contract local shape must be positive integers")
        coordinates = contract["coordinates"]
        if not isinstance(coordinates, dict) or set(coordinates) != set(dims) - {
            "point"
        }:
            raise ValueError("contract must label every local dimension")
        if contract["dtype"] not in {
            "float32",
            "float64",
            "int32",
            "int64",
            "complex64",
            "complex128",
            "bool",
            "string",
        } or not isinstance(contract["unit"], str):
            raise ValueError("contract requires supported dtype and explicit unit")
        for dim, coordinate in coordinates.items():
            if not isinstance(coordinate, dict) or not isinstance(
                coordinate.get("unit"), str
            ):
                raise ValueError("contract coordinates require values and units")
            values = coordinate.get("values")
            if (
                not isinstance(values, list)
                or len(values) != shape[dims.index(dim)]
                or any(value is None for value in values)
            ):
                raise ValueError("contract coordinates must match local dimensions")
        _finite(contract)
        _json(contract)


def _validate_output_contract(
    unit: dict, name: str, block: dict, dims: list[str], shape: list[int]
) -> None:
    contract = unit.get("output_contracts", {}).get(name)
    if "output_contracts" in unit and contract is None:
        raise ValueError("output was not declared by the work contract")
    if contract is not None:
        for field in ("dtype", "unit", "dims"):
            if block[field] != contract[field]:
                raise ValueError(f"output {field} differs from its work contract")
        for dim, actual, expected in zip(dims, shape, contract["shape"], strict=True):
            if dim != "point" and actual != expected:
                raise ValueError("output local shape differs from its work contract")
        local_coordinates = {
            dim: value
            for dim, value in block.get("coordinates", {}).items()
            if dim != "point"
        }
        if local_coordinates != contract["coordinates"]:
            raise ValueError("output coordinates differ from its work contract")
