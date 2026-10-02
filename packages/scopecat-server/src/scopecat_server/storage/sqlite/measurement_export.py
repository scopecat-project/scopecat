"""Capture an acquisition log from one SQLite snapshot, without execution."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import cast

from scopecat.measurements.archive import RecordSelection, write_measurement_snapshot
from scopecat.measurements.recording_arrow import decode_measurement_append
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetHeader,
)

from scopecat_server.storage.sqlite.object_store import ImmutableObjectStore
from scopecat_server.storage.sqlite.resource_objects import resource_directory
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository


def export_measurement_snapshot(
    runs: SQLiteRunRepository, run_id: str, destination: Path
) -> None:
    """Export committed physical history, including superseded acquisitions.

    The read transaction fixes the header and append index while acquisition can
    continue through WAL. Immutable bytes are read one append at a time. Concurrent
    destructive cleanup can make this export fail, but cannot publish a partial
    destination. This exports a recording, not the run's full evidence closure.
    """
    objects = ImmutableObjectStore(resource_directory(runs.objects, "run", run_id))
    with runs.sqlite.read_transaction() as connection:
        row = cast(
            "sqlite3.Row | None",
            connection.execute(
                "SELECT h.content_hash, r.digest FROM execution_measurement_headers h "
                "JOIN run_repository_refs r ON r.run_id=h.run_id AND r.ref=h.ref "
                "WHERE h.run_id=?",
                (run_id,),
            ).fetchone(),
        )
        if row is None:
            raise ValueError(f"run has no retained recording header: {run_id}")
        header = MeasurementDatasetHeader.model_validate_json(
            objects.read(cast("str", row["digest"]))
        )
        if header.run_id != run_id or header.content_hash != row["content_hash"]:
            raise ValueError("recording header does not match its retained index")

        def appends() -> Iterator[MeasurementDatasetAppend]:
            rows = cast(
                "Iterator[sqlite3.Row]",
                connection.execute(
                    "SELECT a.acquisition_start, a.record_count, "
                    "a.content_hash, r.digest "
                    "FROM execution_measurement_appends a "
                    "LEFT JOIN run_repository_refs r "
                    "ON r.run_id=a.run_id AND r.ref=a.ref "
                    "WHERE a.run_id=? ORDER BY a.acquisition_start",
                    (run_id,),
                ),
            )
            for item in rows:
                if item["digest"] is None:
                    raise ValueError("retained recording chunk is missing")
                append = decode_measurement_append(
                    objects.read(cast("str", item["digest"])), header.dataset_schema
                )
                if (
                    append.acquisition_start != item["acquisition_start"]
                    or len(append.records) != item["record_count"]
                    or append.content_hash != item["content_hash"]
                ):
                    raise ValueError(
                        "recording chunk does not match its retained index"
                    )
                yield append

        def projection() -> Iterator[RecordSelection]:
            rows = cast(
                "Iterator[sqlite3.Row]",
                connection.execute(
                    "SELECT point_index, acquisition_index "
                    "FROM execution_measurement_projection WHERE run_id=? "
                    "ORDER BY point_index",
                    (run_id,),
                ),
            )
            for item in rows:
                yield RecordSelection(
                    point_index=cast("int", item["point_index"]),
                    acquisition_index=cast("int", item["acquisition_index"]),
                )

        write_measurement_snapshot(
            destination, header, appends(), projection=projection()
        )
