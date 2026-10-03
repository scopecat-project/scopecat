"""An immutable logical selection referencing retained physical acquisitions."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

MEASUREMENT_SLICE_KIND = "measurement_slice"


class MeasurementSlicePoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    point_index: int = Field(ge=0)
    acquisition_index: int = Field(ge=0)
    record_content_hash: str


class MeasurementSlice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    header_content_hash: str
    points: tuple[MeasurementSlicePoint, ...]

    @model_validator(mode="after")
    def validate_order(self) -> MeasurementSlice:
        indices = tuple(point.point_index for point in self.points)
        if not indices or indices != tuple(sorted(set(indices))):
            raise ValueError(
                "measurement slice requires ordered distinct logical points"
            )
        return self
