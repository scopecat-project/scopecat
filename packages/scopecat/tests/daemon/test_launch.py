from __future__ import annotations

import pytest
from pydantic import ValidationError

from scopecat.application.launch import LaunchInputSchema
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.run import ConfigRegistryRunConfigSource


def test_catalog_retains_project_schema_beyond_console_renderer() -> None:
    original = {
        "properties": {
            "mode": {"type": "integer", "enum": [1, 2]},
            "optional": {"type": ["number", "null"], "default": None},
            "custom": {"$ref": "#/$defs/Custom"},
            "impossible": False,
        },
        "$defs": {"Custom": {"type": "object"}},
    }
    assert (
        LaunchInputSchema.model_validate(original).model_dump(mode="json") == original
    )


@pytest.mark.parametrize(
    "change",
    [
        {"inputs": {"delay_ns": 2}},
        {"sample": "chip-2"},
        {"actor": "second-operator"},
        {"version": "2"},
        {"experiment": "other"},
    ],
)
def test_submission_rejects_a_changed_preview_request(
    change: dict[str, object],
) -> None:
    request = LaunchRequest(action="preview", experiment="timing", version="1")
    with pytest.raises(ValidationError, match="request changed"):
        LaunchRequest.model_validate(
            {
                **request.model_dump(),
                **change,
                "action": "submit",
                "request_key": "retry",
                "expected_request_hash": request.request_hash,
                "config_source": ConfigRegistryRunConfigSource(
                    selector="active",
                    entry_id="baseline",
                    config_ref="baseline",
                    content_hash="sha256:" + "a" * 64,
                    registry_generation=1,
                ),
            }
        )
