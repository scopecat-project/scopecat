"""Exact calibration inputs and independent publication ownership."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from scopecat.records.config import ConfigContentHash
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.run import ConfigRegistryRunConfigSource
from scopecat.records.sample import SampleBinding, SampleSelector


class _ScopeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CatalogCalibrationOwner(_ScopeModel):
    kind: Literal["catalog"] = "catalog"


class WorkingPointCalibrationOwner(_ScopeModel):
    kind: Literal["working_point"] = "working_point"
    workspace_id: str = Field(min_length=1)


type CalibrationOwner = Annotated[
    CatalogCalibrationOwner | WorkingPointCalibrationOwner, Field(discriminator="kind")
]


class CatalogCalibrationScope(_ScopeModel):
    """Saved inputs for nonpublishing checks; no implicit parameter owner."""

    kind: Literal["catalog"] = "catalog"

    @property
    def owner(self) -> CatalogCalibrationOwner:
        return CatalogCalibrationOwner()

    def sample_selectors(self) -> tuple[SampleSelector, ...]:
        return ()


class WorkingPointCalibrationScope(_ScopeModel):
    """One stable workspace and the exact physical scope it owns."""

    kind: Literal["working_point"] = "working_point"
    workspace_id: str = Field(min_length=1)
    sample: SampleBinding

    @property
    def owner(self) -> WorkingPointCalibrationOwner:
        return WorkingPointCalibrationOwner(workspace_id=self.workspace_id)

    def sample_selectors(self) -> tuple[SampleSelector, ...]:
        sample = self.sample
        return (
            SampleSelector(
                role=sample.role,
                sample_id=sample.sample_id,
                revision=sample.revision,
                context_id=sample.context_id,
                batch_id=sample.batch_id,
            ),
        )


type CalibrationScope = Annotated[
    CatalogCalibrationScope | WorkingPointCalibrationScope, Field(discriminator="kind")
]


class CalibrationConfigSourceRef(_ScopeModel):
    """Exact saved configuration and its calibration ownership scope."""

    kind: Literal["config_registry"] = "config_registry"
    entry_id: str = Field(min_length=1)
    config_ref: str = Field(min_length=1)
    content_hash: ConfigContentHash
    scope: CalibrationScope = Field(default_factory=CatalogCalibrationScope)

    @property
    def context_ref(self) -> ConfigContextRef:
        return ConfigContextRef(entry_id=self.entry_id, content_hash=self.content_hash)

    @classmethod
    def from_run_config_source(
        cls,
        source: ConfigRegistryRunConfigSource,
    ) -> CalibrationConfigSourceRef:
        return cls(
            entry_id=source.entry_id,
            config_ref=source.config_ref,
            content_hash=source.content_hash,
        )
