"""The typed launch input shared by preview and checked plan admission."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.author_workspace import AuthorWorkspaceId, absent_workspace
from scopecat.records.config_context import ConfigContextRef, ContextRunConfigSource
from scopecat.records.content import Sha256ContentHash
from scopecat.records.control_edit import ControlEdit
from scopecat.records.experimental_batch import ExperimentalBatchId, absent_batch
from scopecat.records.manual_preview import ManualPreviewFence
from scopecat.records.parameter_update import ParameterUpdate
from scopecat.records.plan_ref import ExperimentPlanRef, PlanConfigRef
from scopecat.records.record_collection import RecordCollectionId
from scopecat.records.request_sweep import ParameterSweep
from scopecat.records.run import (
    AnalysisCandidateRunConfigSource,
    ConfigRegistryRunConfigSource,
)
from scopecat.records.sample import SampleBinding

type LaunchConfigSource = (
    ConfigRegistryRunConfigSource
    | ContextRunConfigSource
    | AnalysisCandidateRunConfigSource
)


def _absent_collection(value: object) -> bool:
    return value is None


class LaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    record_collection: RecordCollectionId | None = Field(
        default=None, exclude_if=_absent_collection
    )
    workspace_id: AuthorWorkspaceId | None = Field(
        default=None, exclude_if=absent_workspace
    )
    code_revision: AuthorRevisionRef | None = None
    action: Literal["list", "preview", "submit"]
    experiment: str = ""
    version: str = ""
    request_key: str = ""
    sample: str | None = None
    batch_id: ExperimentalBatchId | None = Field(default=None, exclude_if=absent_batch)
    actor: str = "operator"
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    control_edits: dict[str, ControlEdit] = Field(default_factory=dict)
    scan_mode: Literal["cartesian", "paired"] = "cartesian"
    parameter_sweeps: tuple[ParameterSweep, ...] = ()
    manual_state: ManualPreviewFence | None = None
    expected_request_hash: Sha256ContentHash | None = None
    context: ConfigContextRef | None = None
    overrides: tuple[ParameterUpdate, ...] = Field(default=(), max_length=256)
    config_source: LaunchConfigSource | None = None
    configuration: PlanConfigRef | None = None
    sample_binding: SampleBinding | None = None
    plan_ref: ExperimentPlanRef | None = None

    @model_validator(mode="after")
    def validate_action(self) -> LaunchRequest:
        if self.action != "list" and not (
            self.experiment and self.version and self.actor.strip()
        ):
            raise ValueError("launch requires experiment, version and actor")
        if isinstance(self.config_source, AnalysisCandidateRunConfigSource) and (
            self.context is not None or self.configuration is not None or self.overrides
        ):
            raise ValueError("candidate already selects its exact configuration")
        if self.configuration is not None and self.context is not None:
            raise ValueError("choose saved configuration or context")
        if (
            self.sample_binding is not None
            and self.sample != self.sample_binding.sample_id
        ):
            raise ValueError("sample name does not match the exact sample binding")
        if self.overrides and self.context is None:
            raise ValueError("parameter overrides require an explicit context")
        if self.action == "submit":
            if not self.request_key.strip() or self.expected_request_hash is None:
                raise ValueError(
                    "submit requires a request key and preview request hash"
                )
            if self.config_source is None or (
                isinstance(
                    self.config_source,
                    ConfigRegistryRunConfigSource | AnalysisCandidateRunConfigSource,
                )
                and self.config_source.registry_generation is None
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
                "experiment": self.experiment,
                **({"workspace_id": self.workspace_id} if self.workspace_id else {}),
                "version": self.version,
                "inputs": self.inputs,
                **(
                    {"scan_mode": self.scan_mode}
                    if self.scan_mode != "cartesian"
                    else {}
                ),
                **(
                    {
                        "parameter_sweeps": [
                            s.model_dump(mode="json") for s in self.parameter_sweeps
                        ]
                    }
                    if self.parameter_sweeps
                    else {}
                ),
                "sample": self.sample,
                **({"batch_id": self.batch_id} if self.batch_id is not None else {}),
                "actor": self.actor,
                **(
                    {"record_collection": self.record_collection}
                    if self.record_collection is not None
                    else {}
                ),
                **(
                    {
                        "candidate": self.config_source.model_dump(
                            mode="json", exclude={"registry_generation"}
                        )
                    }
                    if isinstance(self.config_source, AnalysisCandidateRunConfigSource)
                    else {}
                ),
                **(
                    {"configuration": self.configuration.model_dump(mode="json")}
                    if self.configuration
                    else {}
                ),
                **(
                    {"sample_binding": self.sample_binding.model_dump(mode="json")}
                    if self.sample_binding
                    else {}
                ),
                **(
                    {"plan_ref": self.plan_ref.model_dump(mode="json")}
                    if self.plan_ref
                    else {}
                ),
                **(
                    {
                        "context": self.context.model_dump(mode="json"),
                        "overrides": [
                            edit.model_dump(mode="json") for edit in self.overrides
                        ],
                    }
                    if self.context is not None
                    else {}
                ),
                **(
                    {
                        "control_edits": {
                            name: edit.model_dump(mode="json")
                            for name, edit in self.control_edits.items()
                        }
                    }
                    if self.control_edits
                    else {}
                ),
            }
        )
