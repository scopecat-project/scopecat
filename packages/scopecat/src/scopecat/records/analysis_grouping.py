from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class AnalysisGrouping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    by: tuple[str, ...]
    fitting: str
    repeats: Literal["separate", "combine"] = "separate"

    @model_validator(mode="after")
    def validate_axes(self) -> AnalysisGrouping:
        if (
            not self.fitting
            or self.fitting in self.by
            or len(set(self.by)) != len(self.by)
        ):
            raise ValueError(
                "fitting coordinate must be distinct from unique group coordinates"
            )
        return self
