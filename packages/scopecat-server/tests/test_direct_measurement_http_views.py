"""Bounded UI and streamed legacy routes over a synthetic offline instrument."""

from pathlib import Path
from typing import Literal

import pytest
from fastapi.testclient import TestClient
from scopecat.daemon.hardware_receipt_wire import decode_collect_receipt
from scopecat.daemon.wire import InstrumentSessionOpenCommand
from scopecat.kernel.problems import Problem, ProblemPhase, problem
from scopecat_testkit.config_registry import load_config
from scopecat_testkit.server.instruments import signal_endpoint

from scopecat_server import LocalDaemonRuntime
from scopecat_server.instruments.retained_receipt import RawCollectReceipt


@pytest.mark.parametrize("policy", ["memory_only", "allow_temporary_disk"])
def test_review_preview_save_and_binary_reads_never_eagerly_materialize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy: Literal["memory_only", "allow_temporary_disk"],
) -> None:
    def forbid(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("HTTP review and original streaming must not materialize")

    with (
        LocalDaemonRuntime(
            tmp_path,
            bootstrap_config=load_config(),
            instrument_endpoint=signal_endpoint(),
        ) as runtime,
        TestClient(runtime.app()) as client,
    ):
        monkeypatch.setattr(RawCollectReceipt, "materialize", forbid)
        monkeypatch.setattr(RawCollectReceipt, "to_bytes", forbid)
        response = client.put(
            "/api/v1/pending-result-settings",
            json={"revision": 0, "storage_policy": policy},
        )
        assert response.status_code == 200, response.text
        assert client.get("/api/v1/pending-result-settings").json() == response.json()
        session = runtime.application.instruments.open_session(
            InstrumentSessionOpenCommand(
                operation_id="bounded-http",
                actor="test",
                instrument_ids=("source-0",),
                setup=runtime.application.setup.resolve("initial").ref,
            )
        )
        root = f"/api/v1/instrument-sessions/{session.session_id}/instruments/source-0"
        intent = {
            "command_id": "sample",
            "instrument_id": "source-0",
            "interface_id": "test.scalar_signal/v1",
            "acquisition_id": "sample",
            "result_ids": ["signal"],
            "retain_result": True,
        }
        review = client.post(f"{root}/collect-review", json=intent)
        assert review.status_code == 200, review.text
        summary = review.json()
        assert summary["status"] == "collected"
        assert "readback" not in summary
        assert summary["pending"]["raw_bytes"] > 0
        selection = summary["pending"]["selection"]
        identity = {
            key: selection[key] for key in ("session_id", "instrument_id", "command_id")
        }
        assert client.post(f"{root}/collect-review", json=intent).json() == summary

        # Raw legacy retrieval is still byte-exact without allocating a full
        # encoded carrier on the server. Closing control does not revoke data.
        binary = client.post(f"{root}/collect", json=intent)
        assert binary.status_code == 200, binary.text
        assert len(binary.content) == int(binary.headers["content-length"])
        original = decode_collect_receipt(binary.content)
        assert original.status == "collected"
        runtime.application.instruments.close_session(session.session_id)
        preview = client.post(
            "/api/v1/direct-measurements/pending/preview",
            json={**identity, "start": 0, "limit": 1},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["method"] == "exact_slice"
        [field] = preview.json()["fields"]
        assert field["complete"] is True
        assert field["returned_count"] == 1
        assert (
            client.post(
                "/api/v1/direct-measurements/pending/preview",
                json={**identity, "request_ids": ["absent"]},
            ).status_code
            == 422
        )
        pending = client.post(
            "/api/v1/direct-measurements/pending/receipt", json=identity
        )
        assert pending.content == binary.content
        saved = client.post("/api/v1/direct-measurements", json=selection)
        assert saved.status_code == 200, saved.text
        record_id = saved.json()["id"]
        assert (
            client.get(f"/api/v1/direct-measurements/{record_id}/receipt").content
            == binary.content
        )
        saved_preview = client.post(
            f"/api/v1/direct-measurements/{record_id}/preview",
            json={"start": 0, "limit": 1},
        )
        assert saved_preview.status_code == 200, saved_preview.text
        assert saved_preview.json() == preview.json()
        assert (
            client.post(
                "/api/v1/direct-measurements/pending/preview", json=identity
            ).status_code
            == 404
        )


def test_settings_cas_and_review_retention_precondition(tmp_path: Path) -> None:
    with (
        LocalDaemonRuntime(
            tmp_path,
            bootstrap_config=load_config(),
            instrument_endpoint=signal_endpoint(),
        ) as runtime,
        TestClient(runtime.app()) as client,
    ):
        assert client.get("/api/v1/pending-result-settings").json() == {
            "revision": 0,
            "storage_policy": "memory_only",
        }
        capacity = client.get("/api/v1/pending-result-capacity")
        assert capacity.status_code == 200, capacity.text
        assert capacity.json()["memory_bytes"] == 0
        assert capacity.json()["work_bytes"] == 0
        assert capacity.json()["disk_bytes"] == 0
        assert capacity.json()["memory_limit_bytes"] == 256 * 1024 * 1024
        assert capacity.json()["storage_degraded"] is False
        changed = client.put(
            "/api/v1/pending-result-settings",
            json={"revision": 0, "storage_policy": "allow_temporary_disk"},
        )
        assert changed.status_code == 200
        assert (
            client.put(
                "/api/v1/pending-result-settings",
                json={"revision": 0, "storage_policy": "memory_only"},
            ).status_code
            == 409
        )
        assert client.get("/api/v1/pending-result-settings").json() == changed.json()
        rejected = client.post(
            "/api/v1/instrument-sessions/absent/instruments/source-0/collect-review",
            json={
                "command_id": "never-started",
                "instrument_id": "source-0",
                "interface_id": "test.scalar_signal/v1",
                "acquisition_id": "sample",
                "retain_result": False,
            },
        )
        assert rejected.status_code == 422


@pytest.mark.parametrize(
    ("code", "validation"),
    [
        ("direct_result_contract_view_unavailable", "unverified"),
        ("injected_result_contract_mismatch", "invalid"),
    ],
)
def test_failed_contract_verification_keeps_a_labeled_diagnostic_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str, validation: str
) -> None:
    # Inject only the post-receive verification outcome. This is not a claim
    # that incomplete, corrupt or over-envelope driver frames can be recovered.
    def verification_outcome(*_args: object) -> list[Problem]:
        return [
            problem(code, "Injected verification outcome", phase=ProblemPhase.EXECUTION)
        ]

    monkeypatch.setattr(RawCollectReceipt, "validate_command", verification_outcome)
    with (
        LocalDaemonRuntime(
            tmp_path,
            bootstrap_config=load_config(),
            instrument_endpoint=signal_endpoint(),
        ) as runtime,
        TestClient(runtime.app()) as client,
    ):
        session = runtime.application.instruments.open_session(
            InstrumentSessionOpenCommand(
                operation_id="diagnostic-original",
                actor="test",
                instrument_ids=("source-0",),
                setup=runtime.application.setup.resolve("initial").ref,
            )
        )
        failed = client.post(
            f"/api/v1/instrument-sessions/{session.session_id}/instruments/"
            "source-0/collect-review",
            json={
                "command_id": "diagnostic",
                "instrument_id": "source-0",
                "interface_id": "test.scalar_signal/v1",
                "acquisition_id": "sample",
                "result_ids": ["signal"],
                "retain_result": True,
            },
        )
        assert failed.status_code == 409, failed.text
        [pending] = client.get(
            "/api/v1/direct-measurements/pending", params={"instrument_id": "source-0"}
        ).json()["items"]
        assert pending["receipt_validation"] == validation
        assert pending["validation_code"] == code
        assert pending["validation_reason"] == "Injected verification outcome"
        identity = {
            key: pending["selection"][key]
            for key in ("session_id", "instrument_id", "command_id")
        }
        original = client.post(
            "/api/v1/direct-measurements/pending/receipt", json=identity
        )
        assert original.status_code == 200, original.text
        assert (
            client.post(
                "/api/v1/direct-measurements/pending/preview", json=identity
            ).status_code
            == 409
        )
        saved = client.post("/api/v1/direct-measurements", json=pending["selection"])
        assert saved.status_code == 200, saved.text
        record = saved.json()
        assert record["receipt_validation"] == validation
        assert record["validation_code"] == code
        assert record["selection"] == pending["selection"]
        record_id = record["id"]
        assert (
            client.post(
                f"/api/v1/direct-measurements/{record_id}/preview", json={}
            ).status_code
            == 409
        )
        assert (
            client.get(f"/api/v1/direct-measurements/{record_id}/receipt").content
            == original.content
        )
