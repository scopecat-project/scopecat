"""Bounded non-executing configuration file exchange, separate from run captures."""

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import ValidationError
from scopecat.records.configuration_exchange import (
    ConfigurationDerivation,
    ConfigurationDerive,
    ConfigurationExchange,
    ConfigurationExport,
    ConfigurationImportSummary,
    ConfigurationInspection,
)
from scopecat.records.content import Sha256ContentHash

from scopecat_server.services.configuration_exchange import MAX_EXCHANGE_BYTES

if TYPE_CHECKING:
    from scopecat_server.services.application import DaemonApplication


async def _document(request: Request) -> ConfigurationExchange:
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAX_EXCHANGE_BYTES:
            raise HTTPException(413, "Configuration exchange exceeds the 16 MiB limit")
    try:
        return ConfigurationExchange.model_validate_json(bytes(payload))
    except ValidationError as error:
        raise HTTPException(
            422, "Invalid configuration exchange: " + str(error)
        ) from error


def configuration_exchange_router(application: DaemonApplication) -> APIRouter:
    router = APIRouter(prefix="/api/v1/configuration-exchange")

    @router.post("/export")
    def export(command: ConfigurationExport) -> ConfigurationExchange:
        try:
            return application.configuration_exchange.export(command)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post("/file")
    async def file(request: Request) -> Response:
        try:
            document = await _document(request)
            application.configuration_exchange.inspect(document)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return Response(
            document.model_dump_json(),
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="configuration.json"'
            },
        )

    @router.post("/inspect")
    async def inspect(request: Request) -> ConfigurationInspection:
        try:
            return application.configuration_exchange.inspect(await _document(request))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post("/imports")
    async def retain(request: Request) -> ConfigurationInspection:
        try:
            return application.configuration_exchange.retain(await _document(request))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/imports")
    def imports() -> tuple[ConfigurationImportSummary, ...]:
        return application.configuration_exchange.list()

    @router.get("/imports/{content_hash}")
    def read(content_hash: Sha256ContentHash) -> ConfigurationInspection:
        return application.configuration_exchange.read(content_hash)

    @router.post("/derive")
    def derive(command: ConfigurationDerive) -> ConfigurationDerivation:
        try:
            return application.configuration_exchange.derive(command)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post("/imports/{content_hash}/source")
    def source(content_hash: Sha256ContentHash, accepted: bool = False) -> Response:
        try:
            data = application.configuration_exchange.source_archive(
                content_hash, accepted=accepted
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return Response(
            data,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="author-source.zip"'},
        )

    return router
