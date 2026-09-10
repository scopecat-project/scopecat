"""Record trusted worker footprints and expose their relevant manual-change check."""

from __future__ import annotations

from typing import TYPE_CHECKING

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.config_context import ContextRunConfigSource
from scopecat.records.manual_preview import (
    ManualPreviewBinding,
    ManualPreviewFence,
    ManualPreviewValidity,
    PreviewInstrument,
)
from scopecat.records.run import AnalysisCandidateRunConfigSource

from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.manual_preview import ManualPreviewRepository

if TYPE_CHECKING:
    from scopecat.application.launch import LaunchPreview
    from scopecat.records.launch_request import LaunchRequest

    from scopecat_server.services.config import ConfigService
    from scopecat_server.services.runs import RunService


class ManualPreviewService:
    def __init__(
        self, sqlite: SQLiteDatabase, config: ConfigService, runs: RunService
    ) -> None:
        self.repository = ManualPreviewRepository(sqlite)
        self.config = config
        self.runs = runs

    def cursor(self) -> int:
        return self.repository.cursor()

    def record_preview(
        self,
        preview: LaunchPreview,
        *,
        cursor: int,
    ) -> LaunchPreview:
        source = preview.config_source
        if isinstance(source, AnalysisCandidateRunConfigSource):
            # Candidates change only parameter cells; physical inventory belongs
            # to the exact baseline and was checked by the launch resolver.
            config = self.runs.get_run_config(source.source_run_id).config
        else:
            entry_id = (
                source.context.entry_id
                if isinstance(source, ContextRunConfigSource)
                else source.entry_id
            )
            config = self.config.get_config_entry(entry_id).config
        specs = {spec.id: spec for spec in config.instrument_registry.instruments}
        instruments = tuple(
            PreviewInstrument(
                instrument_id=name, exclusivity_key=specs[name].exclusivity_key
            )
            for name in preview.resources
        )
        fence = self.repository.record(
            cursor=cursor,
            binding=ManualPreviewBinding(
                request_hash=preview.request_hash,
                config_source_hash=sha256_json_hash(source.model_dump(mode="json")),
                code_revision=preview.code_revision,
            ),
            instruments=instruments,
        )
        return preview.model_copy(update={"manual_state": fence})

    @staticmethod
    def require_binding(
        request: LaunchRequest,
    ) -> None:
        fence = request.manual_state
        if fence is None or request.config_source is None:
            raise ValueError("Submit requires the checked preview; preview again")
        expected = ManualPreviewBinding(
            request_hash=request.request_hash,
            config_source_hash=sha256_json_hash(
                request.config_source.model_dump(mode="json")
            ),
            code_revision=request.code_revision,
        )
        if fence.binding != expected:
            raise ValueError("Checked preview binding changed; preview again")
        # Mutation checks happen in procedure admission, after exact-key replay.

    def validity(self, fence: ManualPreviewFence) -> ManualPreviewValidity:
        return self.repository.validity(fence)
