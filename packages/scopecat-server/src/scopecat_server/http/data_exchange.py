"""Stream local file uploads without accepting client-supplied filesystem paths."""

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal
from zipfile import BadZipFile

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from scopecat.data_exchange.models import (
    CaptureImportReceipt,
    CaptureRecordingPage,
    CaptureSummary,
    ScientificEvidence,
)
from starlette.concurrency import run_in_threadpool

if TYPE_CHECKING:
    from scopecat_server.services.application import DaemonApplication

MAX_CAPTURE_UPLOAD_BYTES = 64 * 1024**3


def data_exchange_router(application: DaemonApplication) -> APIRouter:
    router = APIRouter(prefix="/api/v1/data/captures")

    @router.post(
        "",
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/octet-stream": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                },
            }
        },
    )
    async def import_capture(request: Request) -> CaptureImportReceipt:
        if (
            request.headers.get("content-type", "").split(";")[0]
            != "application/octet-stream"
        ):
            raise HTTPException(
                415, "upload a Scopecat file as application/octet-stream"
            )
        with tempfile.TemporaryDirectory(prefix="scopecat-upload-") as temporary:
            source = Path(temporary) / "capture.scopecat"
            size = 0
            with source.open("wb") as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_CAPTURE_UPLOAD_BYTES:
                        raise HTTPException(
                            413, "capture exceeds the 64 GiB upload limit"
                        )
                    await run_in_threadpool(output.write, chunk)
            try:
                return await run_in_threadpool(
                    application.data_exchange.import_file, source
                )
            except (ValueError, BadZipFile, KeyError) as error:
                raise HTTPException(422, f"invalid capture: {error}") from error

    @router.get("")
    def list_captures(
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> tuple[CaptureSummary, ...]:
        return application.data_exchange.list(offset=offset, limit=limit)

    @router.get("/{content_hash}/evidence")
    def read_evidence(content_hash: str) -> ScientificEvidence:
        return application.data_exchange.evidence(content_hash)

    @router.get("/{content_hash}/file")
    def download_capture(content_hash: str) -> FileResponse:
        return FileResponse(
            application.data_exchange.download(content_hash),
            media_type="application/octet-stream",
            filename="capture.scopecat",
        )

    @router.get("/{content_hash}/runs/{run_id}/recording")
    def read_recording(
        content_hash: str,
        run_id: str,
        selection: Literal["acquired", "selected"] = "acquired",
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> CaptureRecordingPage:
        return application.data_exchange.recording_page(
            content_hash, run_id, selection=selection, offset=offset, limit=limit
        )

    return router
