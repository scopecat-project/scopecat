"""Shared binding checks are declaration-only and fence changing device heads."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from reference_lab.provider import ReferenceLabProvider
from scopecat.records.instrument_bindings import (
    BindingCheck,
    BindingOptions,
    BindingRequirements,
    InstrumentBindingChoice,
    InstrumentBindingPlan,
    InstrumentRequirement,
)
from scopecat.sdk.instruments import InstrumentBackend

from scopecat_server.instruments.backend import LocalInstrumentBackendEndpoint
from scopecat_server.runtime import LocalDaemonRuntime


def test_binding_checks_are_pure_and_reject_stale_or_unknown_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    provider = ReferenceLabProvider()
    endpoint = LocalInstrumentBackendEndpoint(
        InstrumentBackend(provider=provider, driver_catalog=provider.driver_catalog)
    )
    with (
        LocalDaemonRuntime(tmp_path, instrument_endpoint=endpoint) as runtime,
        TestClient(runtime.app()) as client,
    ):
        driver = runtime.application.devices.driver_ref(
            "reference_lab.virtual.multichannel_dc_source"
        )
        command: dict[str, Any] = {
            "device_id": "dc",
            "label": "DC",
            "revision_id": "dc-v1",
            "connection": {
                "driver": driver.model_dump(mode="json"),
                "connection": {"kind": "virtual", "options": {}},
            },
            "expected_head": None,
            "actor": "author",
        }
        registered = client.post("/api/v1/devices", json=command)
        assert registered.status_code == 200, registered.text

        def unexpected(*_args: object, **_kwargs: object) -> None:
            raise AssertionError(
                "binding query/check must not connect or acquire control"
            )

        monkeypatch.setattr(endpoint, "connect", unexpected)
        access_setup = runtime.application.devices.access_setup
        monkeypatch.setattr(runtime.application.devices, "access_setup", unexpected)
        requirements = BindingRequirements(
            workspace_id="test",
            experiment_id="bias",
            version="v1",
            inputs_hash="test",
            supported=True,
            requirements=(
                InstrumentRequirement(
                    port_id="bias", interface_id="scopecat.dc_bias/v1"
                ),
            ),
        )
        response = client.post(
            "/api/v1/setup/bindings/options", json=requirements.model_dump(mode="json")
        )
        assert response.status_code == 200, response.text
        options = BindingOptions.model_validate(response.json())
        assert len(options.candidates) == 2
        assert options.diagnostics[0].code == "selection_required"
        candidate = options.candidates[0]
        choice = InstrumentBindingChoice(
            port_id="bias",
            device_id="dc",
            connection_revision_id=candidate.connection_revision_id,
            component_path=candidate.component_path,
            run_start="preserve",
            success_action="release",
            failure_action="abort_and_release",
        )
        plan = InstrumentBindingPlan(requirements=requirements, choices=(choice,))

        def check(plan: InstrumentBindingPlan) -> BindingCheck:
            response = client.post(
                "/api/v1/setup/bindings/check", json=plan.model_dump(mode="json")
            )
            assert response.status_code == 200, response.text
            return BindingCheck.model_validate(response.json())

        assert check(plan).definition is not None
        assert not runtime.application.setup.definitions()
        assert (
            check(plan.model_copy(update={"choices": (choice, choice)})).definition
            is None
        )
        assert (
            check(
                plan.model_copy(
                    update={
                        "choices": (
                            choice.model_copy(
                                update={"component_path": ("channels", "missing")}
                            ),
                        )
                    }
                )
            ).definition
            is None
        )
        duplicate = requirements.model_copy(
            update={
                "requirements": (
                    requirements.requirements[0],
                    requirements.requirements[0].model_copy(
                        update={"port_id": "other"}
                    ),
                )
            }
        )
        malformed = plan.model_copy(
            update={
                "requirements": duplicate,
                "choices": (
                    choice,
                    choice.model_copy(update={"port_id": "other"}),
                ),
            }
        )
        assert check(malformed).diagnostics[0].code == "invalid_requirements"
        for invalid in (
            duplicate,
            requirements.model_copy(update={"requirements": ()}),
        ):
            response = client.post(
                "/api/v1/setup/bindings/options", json=invalid.model_dump(mode="json")
            )
            assert response.status_code == 200
            assert response.json()["diagnostics"]
            assert not response.json()["candidates"]
        # Device revision changes after candidate selection; checks must not follow it.
        command["expected_head"] = registered.json()["device"]["head"]
        command["revision_id"] = "dc-v2"
        monkeypatch.setattr(runtime.application.devices, "access_setup", access_setup)
        updated = client.post("/api/v1/devices", json=command)
        assert updated.status_code == 200, updated.text
        monkeypatch.setattr(runtime.application.devices, "access_setup", unexpected)
        assert check(plan).definition is None
        assert "revision changed" in check(plan).diagnostics[0].message
        rejected = client.post(
            "/api/v1/setup/bindings/resolve", json=plan.model_dump(mode="json")
        )
        assert rejected.status_code == 409, rejected.text
        assert "revision changed" in rejected.text
        # Source/artifact drift is unknown, never an empty successful declaration.
        monkeypatch.setattr(endpoint, "_artifact_hash", "sha256:" + "0" * 64)
        response = client.post(
            "/api/v1/setup/bindings/options", json=requirements.model_dump(mode="json")
        )
        unknown = BindingOptions.model_validate(response.json())
        assert not unknown.candidates
        assert "declaration_unavailable" in {d.code for d in unknown.diagnostics}
        assert runtime.application.devices.get("dc").owner_id is None
