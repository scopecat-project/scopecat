"""The typed launch input shared by preview and checked plan admission."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.author_workspace import (
    SERVICE_AUTHOR_WORKSPACE,
    AuthorWorkspaceId,
)
from scopecat.records.content import Sha256ContentHash
from scopecat.records.control_edit import ControlEdit
from scopecat.records.manual_preview import ManualPreviewFence
from scopecat.records.plan_ref import ExperimentPlanRef
from scopecat.records.record_collection import RecordCollectionId
from scopecat.records.request_sweep import ParameterSweep
from scopecat.records.run import (
    AnalysisCandidateRunConfigSource,
    ConfigRegistryRunConfigSource,
)
from scopecat.records.scientific_selection import (
    LaunchConfigSource as LaunchConfigSource,
)
from scopecat.records.scientific_selection import (
    ReviewedScientificSelection,
    ScientificSelection,
)


def _absent_collection(value: object) -> bool:
    return value is None


class LaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    record_collection: RecordCollectionId | None = Field(
        default=None, exclude_if=_absent_collection
    )
    workspace_id: AuthorWorkspaceId = SERVICE_AUTHOR_WORKSPACE
    code_revision: AuthorRevisionRef | None = None
    action: Literal["list", "preview", "submit"]
    experiment: str = ""
    version: str = ""
    request_key: str = ""
    selection: ScientificSelection = Field(default_factory=ScientificSelection)
    reviewed: ReviewedScientificSelection | None = None
    actor: str = "operator"
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    control_edits: dict[str, ControlEdit] = Field(default_factory=dict)
    scan_mode: Literal["cartesian", "paired"] = "cartesian"
    parameter_sweeps: tuple[ParameterSweep, ...] = ()
    manual_state: ManualPreviewFence | None = None
    expected_request_hash: Sha256ContentHash | None = None
    plan_ref: ExperimentPlanRef | None = None

    @model_validator(mode="after")
    def validate_action(self) -> LaunchRequest:
        if self.action != "list" and not (
            self.experiment and self.version and self.actor.strip()
        ):
            raise ValueError("launch requires experiment, version and actor")
        if self.action == "submit":
            if not self.request_key.strip() or self.expected_request_hash is None:
                raise ValueError(
                    "submit requires a request key and preview request hash"
                )
            if self.reviewed is None or (
                isinstance(
                    self.reviewed.config_source,
                    ConfigRegistryRunConfigSource | AnalysisCandidateRunConfigSource,
                )
                and self.reviewed.config_source.registry_generation is None
            ):
                raise ValueError("submit requires the preview's configuration binding")
            if self.expected_request_hash != self.request_hash:
                raise ValueError("request changed since preview; preview again")
        return self

    @property
    def request_hash(self) -> Sha256ContentHash:
        """Identify visible intent, excluding retry key and preview fences."""
        return sha256_json_hash(
            {
                "codec": "scopecat.launch-request.v2",
                "experiment": self.experiment,
                "workspace_id": self.workspace_id,
                "version": self.version,
                "inputs": self.inputs,
                "scan_mode": self.scan_mode,
                "parameter_sweeps": [
                    item.model_dump(mode="json") for item in self.parameter_sweeps
                ],
                "selection": self.selection.intent_content(),
                "scientific_binding": self.reviewed.binding.model_dump(mode="json")
                if self.reviewed
                else None,
                "actor": self.actor,
                "record_collection": self.record_collection,
                "plan_ref": self.plan_ref.model_dump(mode="json")
                if self.plan_ref
                else None,
                "control_edits": {
                    name: edit.model_dump(mode="json")
                    for name, edit in self.control_edits.items()
                },
            }
        )
