"""Freeze already committed point mappings without copying acquisition arrays."""

import sqlite3
from typing import cast

from scopecat.kernel.content_identity import sha256_content_hash
from scopecat.records.content import BytesWrite, ContentEntry
from scopecat.records.measurement_recording import (
    CANONICAL_MEASUREMENT_DATASET_REF,
    MeasurementDatasetHeader,
)
from scopecat.records.measurement_slice import (
    MEASUREMENT_SLICE_KIND,
    MeasurementSlice,
    MeasurementSlicePoint,
)
from scopecat.runs.refs import dataset_content_ref
from scopecat.runs.repository import RunContentPublication

from scopecat_server.storage.sqlite.object_store import ImmutableObjectStore
from scopecat_server.storage.sqlite.resource_objects import resource_directory
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository


def freeze_measurement_slice(
    runs: SQLiteRunRepository,
    run_id: str,
    point_indices: tuple[int, ...],
    *,
    max_input_bytes: int,
) -> ContentEntry | None:
    """Return None until every expected logical point has a fixed acquisition."""
    import json

    header = runs.read_model(
        run_id,
        f"{CANONICAL_MEASUREMENT_DATASET_REF}/header.json",
        MeasurementDatasetHeader,
    )
    with runs.sqlite.read_transaction() as connection:
        rows = cast(
            "list[sqlite3.Row]",
            connection.execute(
                "SELECT p.point_index, p.acquisition_index, "
                "r.record_content_hash, refs.digest "
                "FROM execution_measurement_projection p "
                "JOIN execution_measurement_records r "
                "ON r.run_id=p.run_id AND r.acquisition_index=p.acquisition_index "
                "JOIN execution_measurement_appends a "
                "ON a.run_id=r.run_id AND a.acquisition_start=r.acquisition_start "
                "JOIN run_repository_refs refs "
                "ON refs.run_id=a.run_id AND refs.ref=a.ref "
                "WHERE p.run_id=? "
                "AND p.point_index IN (SELECT value FROM json_each(?)) "
                "ORDER BY p.point_index",
                (run_id, json.dumps(point_indices)),
            ).fetchall(),
        )
    if tuple(cast("int", row["point_index"]) for row in rows) != point_indices:
        return None
    # Budget the encoded chunks that must be decoded, including any neighbouring
    # rows. The recording writer already bounds ordinary chunk sizes.
    objects = ImmutableObjectStore(resource_directory(runs.objects, "run", run_id))
    size = sum(
        objects.path_for(digest).stat().st_size
        for digest in {cast("str", row["digest"]) for row in rows}
    )
    if size > max_input_bytes:
        raise ValueError("group input exceeds the configured byte budget")
    selection = MeasurementSlice(
        run_id=run_id,
        header_content_hash=header.content_hash,
        points=tuple(
            MeasurementSlicePoint(
                point_index=cast("int", row["point_index"]),
                acquisition_index=cast("int", row["acquisition_index"]),
                record_content_hash=cast("str", row["record_content_hash"]),
            )
            for row in rows
        ),
    )
    content = selection.model_dump_json().encode()
    digest = sha256_content_hash(content)
    entry = ContentEntry(
        role="dataset",
        id=f"slice-{digest.removeprefix('sha256:')}",
        kind=MEASUREMENT_SLICE_KIND,
        content_hash=digest,
        metadata={"partial": True},
    )
    runs.publish_content(
        RunContentPublication(
            run_id=run_id,
            entries=(entry,),
            bytes=(
                BytesWrite(
                    ref=dataset_content_ref(dataset_id=entry.id, kind=entry.kind),
                    content=content,
                    replace=False,
                ),
            ),
        )
    )
    return entry
