from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

from scopecat.measurements.archive import (
    MeasurementSnapshot,
    write_measurement_snapshot,
)
from scopecat.records.measurement import (
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementPointCloudPointDomain,
    MeasurementPointDomainColumn,
    MeasurementRecord,
    MeasurementScalar,
    MeasurementVariable,
)
from scopecat.records.measurement_recording import (
    MeasurementDatasetAppend,
    MeasurementDatasetHeader,
)


def recording():
    schema = MeasurementDatasetSchema(
        dataset_id="raw-measurements",
        point_domain=MeasurementPointCloudPointDomain(
            columns=(MeasurementPointDomainColumn(id="bias"),)
        ),
        dimensions=(MeasurementDimension(id="point", kind="point", size=4),),
        variables=(
            MeasurementVariable(
                id="bias", role="coordinate", dtype="float64", unit="V", dims=("point",)
            ),
            MeasurementVariable(
                id="iq", role="observable", dtype="complex128", dims=("point",)
            ),
        ),
    )
    header = MeasurementDatasetHeader(
        run_id="synthetic",
        recording_contract_fingerprint="test-contract",
        dataset_schema=schema,
        expected_record_count=4,
        record_count_limit=4,
    )
    records = tuple(
        MeasurementRecord(
            run_id=header.run_id,
            logical_point_id=f"point-{i}",
            point_index=i,
            coordinates={
                "bias": MeasurementScalar.create(dtype="float64", unit="V", value=i)
            },
            observables={
                "iq": MeasurementScalar.create(dtype="complex128", value=complex(i, -i))
            },
        )
        for i in (2, 0, 3, 1)
    )
    appends = tuple(
        MeasurementDatasetAppend(
            run_id=header.run_id,
            header_content_hash=header.content_hash,
            acquisition_start=start,
            records=records[start : start + 2],
        )
        for start in (0, 2)
    )
    return header, appends, records


def test_snapshot_relocates_and_preserves_acquisition_order(tmp_path):
    header, appends, records = recording()
    original = tmp_path / "measurement.scopecat"
    write_measurement_snapshot(original, header, iter(appends))
    moved = original.rename(tmp_path / "独立数据 snapshot.scopecat")
    with MeasurementSnapshot(moved) as snapshot:
        assert snapshot.header == header
        assert snapshot.record_count == 4
        assert tuple(snapshot.records(offset=1, limit=2)) == records[1:3]
        assert tuple(snapshot.records(offset=4)) == ()
        assert tuple(snapshot.records(limit=0)) == ()


def test_failed_export_leaves_existing_destination_untouched(tmp_path):
    header, appends, _ = recording()
    target = tmp_path / "important.scopecat"
    target.write_bytes(b"existing user data")
    with pytest.raises(FileExistsError):
        write_measurement_snapshot(target, header, appends)
    assert target.read_bytes() == b"existing user data"
    target = tmp_path / "failed.scopecat"
    with pytest.raises(ValueError, match="identity/order"):
        write_measurement_snapshot(target, header, reversed(appends))
    assert not target.exists()
    assert not list(tmp_path.glob(".measurement-*"))


def rewrite(source: Path, destination: Path, changes: dict[str, bytes]):
    with ZipFile(source) as original, ZipFile(destination, "w") as rewritten:
        for name in original.namelist():
            rewritten.writestr(name, changes.pop(name, original.read(name)))
        for name, content in changes.items():
            rewritten.writestr(name, content)


def test_range_reads_do_not_read_other_chunks_but_check_selected_content(tmp_path):
    header, appends, records = recording()
    source = tmp_path / "source.scopecat"
    write_measurement_snapshot(source, header, appends)
    damaged = tmp_path / "damaged.scopecat"
    with ZipFile(source) as archive:
        content = archive.read("chunks/00000001.arrow")
    rewrite(source, damaged, {"chunks/00000001.arrow": bytes(len(content))})
    with MeasurementSnapshot(damaged) as snapshot:
        assert tuple(snapshot.records(limit=2)) == records[:2]
        with pytest.raises(ValueError, match="checksum"):
            tuple(snapshot.records(offset=2, limit=1))


def test_unknown_members_rejected_without_extraction(tmp_path):
    header, appends, _ = recording()
    source = tmp_path / "source.scopecat"
    write_measurement_snapshot(source, header, appends)
    damaged = tmp_path / "damaged.scopecat"
    rewrite(source, damaged, {"../escaped": b"never extract"})
    with pytest.raises(ValueError, match="unknown members"):
        MeasurementSnapshot(damaged)
    assert not (tmp_path / "escaped").exists()


def test_reader_needs_no_server_or_original_project(tmp_path):
    header, appends, _ = recording()
    source = tmp_path / "independent.scopecat"
    write_measurement_snapshot(source, header, appends)
    script = """
import sys
from pathlib import Path
class ForbidServer:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'scopecat_server' or fullname.startswith('scopecat_server.'):
            raise AssertionError('reading data must not import the execution server')
sys.meta_path.insert(0, ForbidServer())
from scopecat.measurements.archive import MeasurementSnapshot
with MeasurementSnapshot(Path(sys.argv[1])) as snapshot:
    assert [r.point_index for r in snapshot.records()] == [2, 0, 3, 1]
"""
    subprocess.run(  # noqa: S603 - fixed isolated interpreter and test script
        [sys.executable, "-I", "-c", script, str(source)],
        cwd=tmp_path,
        check=True,
        timeout=30,
    )


def test_partial_recording_keeps_planned_count(tmp_path):
    header, appends, records = recording()
    source = tmp_path / "partial.scopecat"
    write_measurement_snapshot(source, header, appends[:1])
    with MeasurementSnapshot(source) as snapshot:
        assert snapshot.header.expected_record_count == 4
        assert snapshot.record_count == 2
        assert tuple(snapshot.records()) == records[:2]
