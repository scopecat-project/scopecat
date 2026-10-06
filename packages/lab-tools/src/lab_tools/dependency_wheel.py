"""Deterministic RECORD and ZIP writing for the two locked desktop wheel repairs."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import zipfile
from pathlib import Path


def write_wheel(files: dict[str, bytes], destination: Path, record: str) -> None:
    del files[record]
    rows = io.StringIO(newline="")
    writer = csv.writer(rows, lineterminator="\n")
    for name, data in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
        writer.writerow((name, "sha256=" + digest.decode(), str(len(data))))
    writer.writerow((record, "", ""))
    files[record] = rows.getvalue().encode()
    # Fixed timestamps and uncompressed members make the wheel bytes reproducible
    # across build hosts, independent of their zlib version.
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
