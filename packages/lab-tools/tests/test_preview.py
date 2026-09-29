"""Pinned network artifacts must be verified before extraction or reuse."""

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from lab_tools.preview import download, unpack_gui


def test_download_rejects_unpinned_or_non_https_artifacts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        download("file:///secret", "0" * 64, tmp_path / "result")
    with pytest.raises(ValueError, match="SHA-256"):
        download("https://example.com/preview.json", "../invalid", tmp_path / "result")
    assert not (tmp_path / "result").exists()


@pytest.mark.parametrize("member", ["../outside", "/absolute", "C:relative", "a\\b"])
def test_preview_rejects_archive_escape(tmp_path: Path, member: str) -> None:
    archive = tmp_path / "scopecat-ui.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr(member, "content")
    (tmp_path / "preview.json").write_text(
        json.dumps(
            {
                "format": 1,
                "commit": "a" * 40,
                "packages": {},
                "files": {
                    archive.name: hashlib.sha256(archive.read_bytes()).hexdigest()
                },
            }
        )
    )
    with pytest.raises(ValueError, match="archive path"):
        unpack_gui(tmp_path, tmp_path / "gui")
    assert not (tmp_path / "gui").exists()
