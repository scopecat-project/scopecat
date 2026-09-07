"""Compare full and selected native chunk reads without a device or dataset load."""

# PyArrow's native API has no complete typing stubs.
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import argparse
import gc
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import cast

import psutil
import pyarrow as pa

from benchmarks.record import BENCHMARK_RESULT_PREFIX, benchmark_record_header
from scopecat.kernel.entity import EntityRef
from scopecat.measurements.dataset import Dataset
from scopecat.measurements.entity_selection import (
    MeasurementEntitySelection,
    bind_entity_selection,
)
from scopecat.measurements.recording_arrow import (
    decode_measurement_record_indices,
    encode_measurement_append,
)
from scopecat.records.content import ContentEntry
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementDataset,
    MeasurementDatasetSchema,
)
from scopecat.records.measurement_recording import MeasurementDatasetAppend


def _read(directory: Path, selected: bool, repeats: int) -> dict[str, object]:
    schema = MeasurementDatasetSchema.model_validate_json(
        (directory / "schema.json").read_text()
    )
    selection = (
        MeasurementEntitySelection(
            dimension_id="entity",
            entities=(
                EntityRef(id="q7", kind="qubit"),
                EntityRef(id="q0", kind="qubit"),
            ),
        )
        if selected
        else None
    )
    projected_schema = (
        schema if selection is None else bind_entity_selection(schema, selection).schema
    )
    entry = ContentEntry(
        role="dataset",
        id="raw-measurements",
        kind="measurement_dataset",
        schema=projected_schema.model_dump(mode="json"),
        content_hash="benchmark",
    )
    process = psutil.Process()
    baseline = cast("int", process.memory_info().rss)
    peak = baseline
    stop = threading.Event()

    def sample() -> None:
        nonlocal peak
        while not stop.wait(0.001):
            peak = max(peak, cast("int", process.memory_info().rss))

    sampler = threading.Thread(target=sample)
    sampler.start()
    started = time.perf_counter()
    selected_bytes = wire_bytes = decoded_bytes = blob_bytes = 0
    try:
        for _ in range(repeats):
            blob = (directory / "append.arrow").read_bytes()
            blob_bytes = len(blob)
            decoded_bytes = pa.ipc.open_file(blob).get_batch(0).nbytes
            records = decode_measurement_record_indices(
                blob,
                schema,
                (0,),
                variable_ids=("signal",),
                entity_selection=selection,
            )
            selected_bytes = sum(
                value.values.nbytes
                for record in records
                for value in record.observables.values()
                if isinstance(value, MeasurementArray)
            )
            dataset = Dataset(
                MeasurementDataset(dataset_schema=projected_schema, records=records),
                entry,
            )
            table = dataset.project({"signal": "signal"}, diagnostics="full").to_arrow()
            sink = pa.BufferOutputStream()
            with pa.ipc.new_stream(sink, table.schema) as writer:
                writer.write_table(table)
            wire_bytes = sink.getvalue().size
            peak = max(peak, cast("int", process.memory_info().rss))
            del blob, records, dataset, table, sink
    finally:
        stop.set()
        sampler.join()
    gc.collect()
    return {
        "selected": selected,
        "repeated_pages": repeats,
        "chunk_blob_bytes": blob_bytes,
        "arrow_decoded_chunk_logical_bytes": decoded_bytes,
        "selected_value_working_set_bytes": selected_bytes,
        "arrow_response_payload_bytes": wire_bytes,
        "rss_baseline_bytes": baseline,
        "rss_sampled_peak_bytes": peak,
        "rss_after_gc_bytes": cast("int", process.memory_info().rss),
        "elapsed_s": time.perf_counter() - started,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entities", type=int, default=128)
    parser.add_argument("--samples", type=int, default=4096)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--read-directory", type=Path)
    parser.add_argument("--selected", action="store_true")
    args = parser.parse_args()
    directory = cast("Path | None", args.read_directory)
    repeats = cast("int", args.repeats)
    if directory is not None:
        print(json.dumps(_read(directory, cast("bool", args.selected), repeats)))
        return
    from scopecat_testkit.entity_reads import wide_entity_measurements

    entity_count, sample_count = cast("int", args.entities), cast("int", args.samples)
    if entity_count < 8 or sample_count < 1 or repeats < 1:
        parser.error("requires entities >= 8, samples >= 1, repeats >= 1")
    with tempfile.TemporaryDirectory(prefix="scopecat-entity-reads-") as temporary:
        directory = Path(temporary)
        raw = wide_entity_measurements(
            entity_order=tuple(range(entity_count)),
            point_count=4,
            sample_count=sample_count,
        )
        (directory / "schema.json").write_text(raw.dataset_schema.model_dump_json())
        (directory / "append.arrow").write_bytes(
            encode_measurement_append(
                MeasurementDatasetAppend(
                    run_id="run-wide",
                    header_content_hash="benchmark",
                    acquisition_start=0,
                    records=tuple(raw.records),
                ),
                raw.dataset_schema,
            )
        )
        results: list[object] = []
        for selected in (False, True):
            completed = subprocess.run(  # noqa: S603
                [
                    sys.executable,
                    "-m",
                    "benchmarks.component.entity_reads",
                    "--read-directory",
                    str(directory),
                    "--repeats",
                    str(repeats),
                    *(["--selected"] if selected else []),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            results.append(cast("object", json.loads(completed.stdout)))
    print(
        BENCHMARK_RESULT_PREFIX
        + json.dumps(
            {
                **benchmark_record_header(
                    case_id="entity-reads", case_version=1, kind="component"
                ),
                "platform": sys.platform,
                "entities": entity_count,
                "samples": sample_count,
                "method": (
                    "Separate reader subprocess per width; psutil RSS sampled "
                    "every 1 ms "
                    "and each page. Includes whole immutable blob and Arrow chunk; "
                    "payload bytes exclude HTTP headers. Repeated reads retain no "
                    "previous pages. RSS is sampled, "
                    "not a guaranteed OS high-water mark."
                ),
                "results": results,
            }
        )
    )


if __name__ == "__main__":
    main()
