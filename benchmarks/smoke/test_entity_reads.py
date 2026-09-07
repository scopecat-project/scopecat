from __future__ import annotations

import json
import subprocess
import sys
from typing import cast


def test_entity_read_benchmark_separates_chunk_and_selected_payload_costs() -> None:
    completed = subprocess.run(
        (
            sys.executable,
            "-m",
            "benchmarks",
            "run",
            "entity-reads",
            "--entities",
            "8",
            "--samples",
            "32",
            "--repeats",
            "3",
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    line = next(
        line
        for line in completed.stdout.splitlines()
        if line.startswith("BENCHMARK_RESULT=")
    )
    result = cast(
        "dict[str, object]", json.loads(line.removeprefix("BENCHMARK_RESULT="))
    )
    assert result["case_id"] == "entity-reads"
    assert result["kind"] == "component"
    full, selected = cast("list[dict[str, object]]", result["results"])
    assert full["selected"] is False
    assert selected["selected"] is True
    assert full["repeated_pages"] == selected["repeated_pages"] == 3
    assert full["chunk_blob_bytes"] == selected["chunk_blob_bytes"]
    assert (
        full["arrow_decoded_chunk_logical_bytes"]
        == selected["arrow_decoded_chunk_logical_bytes"]
    )
    assert full["selected_value_working_set_bytes"] == 8 * 32 * 8
    assert selected["selected_value_working_set_bytes"] == 2 * 32 * 8
    assert cast("int", selected["arrow_response_payload_bytes"]) < cast(
        "int", full["arrow_response_payload_bytes"]
    )
    # Host-dependent RSS and timing are measured facts, not CI ratio thresholds.
    assert "rss_sampled_peak_bytes" in full and "rss_sampled_peak_bytes" in selected
