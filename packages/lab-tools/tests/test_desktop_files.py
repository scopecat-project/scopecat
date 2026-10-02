"""Native file transfer publishes complete downloads and preserves failed targets."""

import threading
from pathlib import Path
from unittest.mock import Mock

import httpx2
import pytest

from lab_tools.desktop import DesktopAPI
from lab_tools.desktop_files import import_capture, save_capture
from lab_tools.desktop_session import DesktopSession

HASH = "sha256:" + "a" * 64


def _transport(monkeypatch, handler):
    client = httpx2.Client
    monkeypatch.setattr(
        "lab_tools.desktop_files.httpx2.Client",
        lambda **kwargs: client(transport=httpx2.MockTransport(handler), **kwargs),
    )


def test_native_import_streams_file_and_validates_receipt(tmp_path, monkeypatch):
    content = b"capture" * 200_000
    source = tmp_path / "input.scopecat"
    source.write_bytes(content)

    def handle(request):
        assert request.method == "POST"
        assert request.headers["content-type"] == "application/octet-stream"
        assert request.read() == content
        return httpx2.Response(
            200,
            json={
                "capture": {
                    "content_hash": HASH,
                    "source_project_id": "source",
                    "roots": ["run"],
                },
                "created": True,
            },
        )

    _transport(monkeypatch, handle)
    receipt = import_capture("http://localhost:1234", source)
    assert receipt.created
    assert receipt.capture.content_hash == HASH


def test_native_save_replaces_only_after_completed_download(tmp_path, monkeypatch):
    target = tmp_path / "output.scopecat"
    target.write_bytes(b"previous")
    _transport(monkeypatch, lambda _request: httpx2.Response(200, content=b"complete"))
    save_capture("http://localhost:1234", HASH, target)
    assert target.read_bytes() == b"complete"
    assert sorted(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("failure", ["server", "interrupted"])
def test_native_save_failure_preserves_existing_file(tmp_path, monkeypatch, failure):
    target = tmp_path / "output.scopecat"
    target.write_bytes(b"previous")

    class Interrupted(httpx2.SyncByteStream):
        def __iter__(self):
            yield b"partial"
            raise httpx2.ReadError("connection closed")

    def handle(request):
        if failure == "server":
            return httpx2.Response(409, json={"detail": "capture unavailable"})
        return httpx2.Response(200, stream=Interrupted())

    _transport(monkeypatch, handle)
    with pytest.raises((ValueError, httpx2.ReadError)):
        save_capture("http://localhost:1234", HASH, target)
    assert target.read_bytes() == b"previous"
    assert sorted(tmp_path.iterdir()) == [target]


def test_native_dialog_cancel_does_not_transfer_or_restart(tmp_path, monkeypatch):
    runtime, window = Mock(), Mock()
    session = DesktopSession(runtime, threading.Event())
    session.connected("http://localhost:1234")
    api = DesktopAPI(session, lambda: window)
    upload, download = Mock(), Mock()
    monkeypatch.setattr("lab_tools.desktop_files.import_capture", upload)
    monkeypatch.setattr("lab_tools.desktop_files.save_capture", download)
    window.create_file_dialog.return_value = None
    assert api.open_capture() is None
    assert api.save_capture(HASH) is None
    upload.assert_not_called()
    download.assert_not_called()
    target = tmp_path / "chosen.scopecat"
    window.create_file_dialog.return_value = (str(target),)
    assert api.save_capture(HASH) == str(target)
    download.assert_called_once_with("http://localhost:1234", HASH, Path(target))
    runtime.start.assert_not_called()
    runtime.stop.assert_not_called()
