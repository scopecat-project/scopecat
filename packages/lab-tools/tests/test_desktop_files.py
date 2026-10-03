"""Native file transfer publishes complete downloads and preserves failed targets."""

import threading
from pathlib import Path
from unittest.mock import ANY, Mock

import httpx2
import pytest

from lab_tools.desktop import DesktopAPI
from lab_tools.desktop_files import (
    export_run,
    import_capture,
    save_capture,
    save_captured_artifact,
)
from lab_tools.desktop_session import DesktopSession

HASH = "sha256:" + "a" * 64


def test_cancelled_download_preserves_destination_and_removes_partial(
    tmp_path, monkeypatch
):
    target = tmp_path / "kept.scopecat"
    target.write_bytes(b"original")
    cancel = threading.Event()

    class Cancelled(httpx2.SyncByteStream):
        def __iter__(self):
            yield b"a" * 1024 * 1024
            cancel.set()
            yield b"b" * 1024 * 1024

    _transport(monkeypatch, lambda _request: httpx2.Response(200, stream=Cancelled()))
    with pytest.raises(ValueError, match="取消"):
        save_capture("http://localhost:1234", HASH, target, cancel)
    assert target.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [target]


def test_title_change_targets_only_its_own_native_window():
    runtime, first, second = Mock(), Mock(), Mock()
    session = DesktopSession(runtime, threading.Event())
    api = DesktopAPI(session, lambda: first)
    other = DesktopAPI(session, lambda: second)
    api.set_window_title("Alpha · source · Scopecat")
    other.set_window_title("Beta · source · Scopecat")
    first.set_title.assert_called_once_with("Alpha · source · Scopecat")
    second.set_title.assert_called_once_with("Beta · source · Scopecat")


def test_file_dialog_uses_connection_selected_after_dialog(tmp_path, monkeypatch):
    runtime, window = Mock(), Mock()
    session = DesktopSession(runtime, threading.Event())
    session.connected("http://localhost:1234")
    api = DesktopAPI(session, lambda: window)
    target = tmp_path / "selected.scopecat"

    def choose(*args, **kwargs):
        session.connected("http://localhost:4321")
        return (str(target),)

    window.create_file_dialog.side_effect = choose
    upload, download = Mock(), Mock()
    monkeypatch.setattr("lab_tools.desktop_files.import_capture", upload)
    monkeypatch.setattr("lab_tools.desktop_files.save_capture", download)
    api.open_capture()
    upload.assert_called_once_with("http://localhost:4321", target, ANY)
    session.connected("http://localhost:1234")
    api.save_capture(HASH)
    download.assert_called_once_with("http://localhost:4321", HASH, target, ANY)


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


@pytest.mark.parametrize("save", [save_capture, export_run])
@pytest.mark.parametrize("failure", ["server", "interrupted"])
def test_native_save_failure_preserves_existing_file(
    tmp_path, monkeypatch, failure, save
):
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
        save("http://localhost:1234", HASH, target)
    assert target.read_bytes() == b"previous"
    assert sorted(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("selection_type", [str, tuple], ids=["cocoa", "path-tuple"])
def test_native_dialog_cancel_does_not_transfer_or_restart(
    tmp_path, monkeypatch, selection_type
):
    runtime, window = Mock(), Mock()
    session = DesktopSession(runtime, threading.Event())
    session.connected("http://localhost:1234")
    api = DesktopAPI(session, lambda: window)
    upload, download, export = Mock(), Mock(), Mock()
    monkeypatch.setattr("lab_tools.desktop_files.import_capture", upload)
    monkeypatch.setattr("lab_tools.desktop_files.save_capture", download)
    monkeypatch.setattr("lab_tools.desktop_files.export_run", export)
    window.create_file_dialog.return_value = None
    assert api.open_capture() is None
    assert api.save_capture(HASH) is None
    assert api.export_run("run") is None
    upload.assert_not_called()
    download.assert_not_called()
    export.assert_not_called()
    target = tmp_path / "chosen.scopecat"
    window.create_file_dialog.return_value = (
        str(target) if selection_type is str else (str(target),)
    )
    assert api.save_capture(HASH) == str(target)
    download.assert_called_once_with("http://localhost:1234", HASH, Path(target), ANY)
    assert api.export_run("run") == str(target)
    export.assert_called_once_with("http://localhost:1234", "run", target, ANY)
    runtime.start.assert_not_called()
    runtime.stop.assert_not_called()


def test_artifact_save_uses_record_identity_and_native_destination(
    tmp_path, monkeypatch
):
    runtime, window = Mock(), Mock()
    session = DesktopSession(runtime, threading.Event())
    session.connected("http://localhost:1234")
    api = DesktopAPI(session, lambda: window)
    requests = []
    fail = False

    def handle(request):
        requests.append(request.url.path)
        if fail:
            return httpx2.Response(404, json={"detail": "Missing artifact"})
        return httpx2.Response(200, content=b"report")

    _transport(monkeypatch, handle)
    window.create_file_dialog.return_value = None
    assert (
        api.save_captured_artifact(HASH, "sha256:record", "report", "report.txt")
        is None
    )
    assert not requests
    target = tmp_path / "chosen.txt"
    window.create_file_dialog.return_value = (str(target),)
    assert api.save_captured_artifact(
        HASH, "sha256:record", "report", "C:\\source\\report.txt"
    ) == str(target)
    assert window.create_file_dialog.call_args.kwargs["save_filename"] == "report.txt"
    assert requests == [
        f"/api/v1/data/captures/{HASH}/analyses/sha256:record/artifacts/report"
    ]
    assert target.read_bytes() == b"report"
    fail = True
    with pytest.raises(ValueError, match="Missing artifact"):
        save_captured_artifact(
            "http://localhost:1234", HASH, "sha256:record", "report", target
        )
    assert target.read_bytes() == b"report"
    assert list(tmp_path.iterdir()) == [target]
