"""Stream local file uploads without accepting client-supplied filesystem paths."""

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal
from zipfile import BadZipFile

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from scopecat.daemon.views import (
    MeasurementTracePreview,
    MeasurementTraceProjectionQuery,
)
from scopecat.data_exchange.models import (
    CaptureImportReceipt,
    CaptureRecordingPage,
    CaptureSummary,
    ScientificEvidence,
)
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

if TYPE_CHECKING:
    from scopecat_server.services.application import DaemonApplication

MAX_CAPTURE_UPLOAD_BYTES = 64 * 1024**3


def data_exchange_router(application: DaemonApplication) -> APIRouter:
    router = APIRouter(prefix="/api/v1/data")

    @router.post(
        "/captures",
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

    @router.get("/captures")
    def list_captures(
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> tuple[CaptureSummary, ...]:
        return application.data_exchange.list(offset=offset, limit=limit)

    @router.get("/captures/{content_hash}/evidence")
    def read_evidence(content_hash: str) -> ScientificEvidence:
        return application.data_exchange.evidence(content_hash)

    @router.get("/captures/{content_hash}/file")
    def download_capture(content_hash: str) -> FileResponse:
        return FileResponse(
            application.data_exchange.download(content_hash),
            media_type="application/octet-stream",
            filename="capture.scopecat",
        )

    @router.get(
        "/captures/{content_hash}/analyses/{analysis_hash}/artifacts/{artifact_id}"
    )
    def download_artifact(
        content_hash: str, analysis_hash: str, artifact_id: str
    ) -> FileResponse:
        temporary = tempfile.TemporaryDirectory(prefix="scopecat-artifact-")
        try:
            destination = Path(temporary.name) / "artifact"
            entry = application.data_exchange.copy_analysis_artifact(
                content_hash, analysis_hash, artifact_id, destination
            )
            return FileResponse(
                destination,
                media_type=entry.media_type or "application/octet-stream",
                filename=entry.filename or artifact_id,
                background=BackgroundTask(temporary.cleanup),
            )
        except BaseException:
            temporary.cleanup()
            raise

    @router.get("/captures/{content_hash}/runs/{run_id}/recording")
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

    @router.post("/captures/{content_hash}/runs/{run_id}/recording/traces")
    def read_traces(
        content_hash: str,
        run_id: str,
        query: MeasurementTraceProjectionQuery,
        selection: Literal["acquired", "selected"] = "acquired",
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 1,
    ) -> MeasurementTracePreview:
        return application.data_exchange.recording_traces(
            content_hash, run_id, query, selection=selection, offset=offset, limit=limit
        )

    @router.get("/runs/{run_id}/file")
    def export_run(run_id: str) -> FileResponse:
        temporary = tempfile.TemporaryDirectory(prefix="scopecat-export-")
        try:
            destination = Path(temporary.name) / "run.scopecat"
            application.data_exchange.export_run(run_id, destination)
            return FileResponse(
                destination,
                media_type="application/octet-stream",
                filename="run.scopecat",
                background=BackgroundTask(temporary.cleanup),
            )
        except BaseException:
            temporary.cleanup()
            raise

    return router
