"""Stream native file selections through the unified application's data API."""

import os
import tempfile
from pathlib import Path
from typing import cast
from urllib.parse import quote

import httpx2

from scopecat.data_exchange.models import CaptureImportReceipt


def _check_response(response: httpx2.Response) -> None:
    if response.is_success:
        return
    response.read()
    try:
        payload = cast("object", response.json())
        detail = (
            cast("dict[str, object]", payload).get("detail")
            if isinstance(payload, dict)
            else None
        )
    except ValueError:
        detail = None
    raise ValueError(
        detail
        if isinstance(detail, str)
        else f"文件操作失败（HTTP {response.status_code}）"
    )


def import_capture(base_url: str, source: Path) -> CaptureImportReceipt:
    with (
        source.open("rb") as stream,
        httpx2.Client(
            timeout=httpx2.Timeout(None, connect=3), trust_env=False
        ) as client,
    ):
        response = client.post(
            base_url + "/api/v1/data/captures",
            headers={"Content-Type": "application/octet-stream"},
            content=iter(lambda: stream.read(1024 * 1024), b""),
        )
        _check_response(response)
        return CaptureImportReceipt.model_validate(response.json())


def save_capture(base_url: str, content_hash: str, destination: Path) -> None:
    _download(
        base_url + "/api/v1/data/captures/" + quote(content_hash, safe="") + "/file",
        destination,
    )


def export_run(base_url: str, run_id: str, destination: Path) -> None:
    _download(
        base_url + "/api/v1/data/runs/" + quote(run_id, safe="") + "/file", destination
    )


def save_captured_artifact(
    base_url: str,
    content_hash: str,
    analysis_hash: str,
    artifact_id: str,
    destination: Path,
) -> None:
    _download(
        base_url
        + "/api/v1/data/captures/"
        + quote(content_hash, safe="")
        + "/analyses/"
        + quote(analysis_hash, safe="")
        + "/artifacts/"
        + quote(artifact_id, safe=""),
        destination,
    )


def _download(url: str, destination: Path) -> None:
    """Publish only a completed download; native Save owns overwrite confirmation."""
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=".scopecat-save-", delete=False
    ) as temporary:
        staged = Path(temporary.name)
        try:
            with (
                httpx2.Client(
                    timeout=httpx2.Timeout(None, connect=3), trust_env=False
                ) as client,
                client.stream(
                    "GET",
                    url,
                ) as response,
            ):
                _check_response(response)
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    temporary.write(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())
        except BaseException:
            temporary.close()
            staged.unlink(missing_ok=True)
            raise
    try:
        staged.replace(destination)
    finally:
        staged.unlink(missing_ok=True)
