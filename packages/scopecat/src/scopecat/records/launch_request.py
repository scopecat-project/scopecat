"""The typed launch input shared by preview and checked plan admission."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.config_context import ConfigContextRef, ContextRunConfigSource
from scopecat.records.content import Sha256ContentHash
from scopecat.records.control_edit import ControlEdit
from scopecat.records.manual_preview import ManualPreviewFence
from scopecat.records.parameter_update import ParameterUpdate
from scopecat.records.plan_ref import ExperimentPlanRef, PlanConfigRef
from scopecat.records.run import ConfigRegistryRunConfigSource
from scopecat.records.sample import SampleBinding

type LaunchConfigSource = ConfigRegistryRunConfigSource | ContextRunConfigSource


class LaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code_revision: AuthorRevisionRef | None = None
    action: Literal["list", "preview", "submit"]
    experiment: str = ""
    version: str = ""
    request_key: str = ""
    sample: str | None = None
    actor: str = "operator"
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    control_edits: dict[str, ControlEdit] = Field(default_factory=dict)
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
                isinstance(self.config_source, ConfigRegistryRunConfigSource)
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
                "version": self.version,
                "inputs": self.inputs,
                "sample": self.sample,
                "actor": self.actor,
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
