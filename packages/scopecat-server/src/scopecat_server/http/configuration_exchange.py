"""Bounded non-executing configuration file exchange, separate from run captures."""

from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, override

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute
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


# The document retains its independent 16 MiB service limit. Actor and bindings
# have no model maximum, so reserve another 16 MiB for the command/JSON envelope
# rather than subtracting command metadata from the supported document capacity.
MAX_DERIVE_REQUEST_BYTES = 2 * MAX_EXCHANGE_BYTES


async def _bounded_body(request: Request, limit: int, detail: str) -> bytes:
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > limit:
            raise HTTPException(413, detail)
        payload.extend(chunk)
    return bytes(payload)


class _DeriveRequest(Request):
    _bounded_payload: bytes | None = None

    @override
    async def body(self) -> bytes:
        if self._bounded_payload is None:
            self._bounded_payload = await _bounded_body(
                self,
                MAX_DERIVE_REQUEST_BYTES,
                "Configuration derive request exceeds the 32 MiB limit",
            )
        return self._bounded_payload


class _DeriveRoute(APIRoute):
    @override
    def get_route_handler(self) -> Callable[[Request], Coroutine[None, None, Response]]:
        handler = super().get_route_handler()

        async def bounded(request: Request) -> Response:
            # Keep FastAPI's JSON/model validation, error locations and schema.
            return await handler(_DeriveRequest(request.scope, request.receive))

        return bounded


async def _document(request: Request) -> ConfigurationExchange:
    payload = await _bounded_body(
        request, MAX_EXCHANGE_BYTES, "Configuration exchange exceeds the 16 MiB limit"
    )
    try:
        return ConfigurationExchange.model_validate_json(payload)
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

    def derive(command: ConfigurationDerive) -> ConfigurationDerivation:
        try:
            return application.configuration_exchange.derive(command)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    router.add_api_route(
        "/derive", derive, methods=["POST"], route_class_override=_DeriveRoute
    )

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
