"""Capture the same experiment inputs for managed launches and durable steps."""

from typing import cast

from pydantic import BaseModel, JsonValue

from scopecat.authoring.experiments import ExperimentRequest, Scan
from scopecat.records.control_edit import ControlEdit
from scopecat.records.run_request import AxisValuesSourceRecord


def capture_request_inputs(
    request: ExperimentRequest[object, object], input_model: type[BaseModel]
) -> tuple[dict[str, JsonValue], dict[str, ControlEdit]]:
    values = request.snapshot()
    edits: dict[str, ControlEdit] = {}
    for control in request.declaration.controls.fields:
        if control.id not in values:
            continue
        value = values.pop(control.id)
        if isinstance(value, Scan):
            if not control.scannable:
                raise ValueError(f"{control.id}: input is not scannable")
            edits[control.id] = ControlEdit(
                mode="scan",
                axis=AxisValuesSourceRecord(
                    values=[control.normalize(item) for item in value.values]
                ),
            )
        else:
            edits[control.id] = ControlEdit(
                mode="fixed", value=control.normalize(value)
            )
    inputs = cast(
        "dict[str, JsonValue]",
        input_model.model_validate(values).model_dump(mode="json"),
    )
    return inputs, edits
