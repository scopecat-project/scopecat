"""Verified, atomic recording import into a caller-selected data directory."""

import os
import shutil
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from scopecat.measurements.archive import MeasurementSnapshot


@dataclass(frozen=True)
class SnapshotImport:
    path: Path
    created: bool
    content_hash: str


def import_measurement_snapshot(source: Path, directory: Path) -> SnapshotImport:
    """Copy and verify data; never load source code or register execution state.

    Equal run identities with different captured content are explicit conflicts,
    including a later acquisition snapshot of the same run. Import never silently
    replaces the earlier capture. Equal content is idempotent even when ZIP bytes
    differ. The directory is a data destination, not an executable project.
    """
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=".import-", dir=directory, delete=False
    ) as temporary:
        staged = Path(temporary.name)
    try:
        # Validate the owned copy, so changes to the source during or after import
        # cannot replace the bytes that were checked before publication.
        shutil.copyfile(source, staged)
        with MeasurementSnapshot(staged) as snapshot:
            snapshot.verify()
            identity = sha256(snapshot.header.run_id.encode()).hexdigest()
            content_hash = snapshot.content_hash
        destination = directory / f"{identity}.scopecat"
        try:
            os.link(staged, destination)
        except FileExistsError:
            with MeasurementSnapshot(destination) as existing:
                if existing.content_hash != content_hash:
                    raise ValueError(
                        "this run already has different imported content; "
                        "the existing capture was retained"
                    ) from None
                existing.verify()
            return SnapshotImport(destination, False, content_hash)
        return SnapshotImport(destination, True, content_hash)
    finally:
        staged.unlink(missing_ok=True)
