"""Configuration sharing without runs; originals never become execution authority."""

import asyncio
import io
import json
import sqlite3
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from scopecat.daemon.wire import ParameterSaveCommand, SetupSaveCommand
from scopecat.project import open_project
from scopecat.project_sources import capture_sources
from scopecat.records.configuration_exchange import (
    ConfigurationDerive,
    ConfigurationExchange,
    ConfigurationExport,
)
from scopecat_testkit.server.instruments import signal_endpoint
from scopecat_testkit.workflow_fixtures import load_config
from starlette.types import Message

from scopecat_server import LocalDaemonRuntime
from scopecat_server.http.configuration_exchange import MAX_DERIVE_REQUEST_BYTES
from scopecat_server.scaffold import write_author_scaffold
from scopecat_server.services.configuration_exchange import MAX_EXCHANGE_BYTES

ROOT = "/api/v1/configuration-exchange"


def seed(runtime: LocalDaemonRuntime) -> ConfigurationExchange:
    config = load_config()
    runtime.application.config.save_parameters(
        ParameterSaveCommand(
            revision_id="starting-values",
            catalog=config.parameter_catalog,
            parameters=config.parameter_snapshot,
            actor="sender",
        )
    )
    return runtime.application.configuration_exchange.export(
        ConfigurationExport(parameter_revision="starting-values", label="Signal inputs")
    )


def test_derive_accepts_full_document_and_request_budgets(tmp_path: Path) -> None:
    with LocalDaemonRuntime(tmp_path) as runtime, TestClient(runtime.app()) as client:
        document = seed(runtime).model_copy(update={"origin_store": ""})
        document = document.model_copy(
            update={
                "origin_store": "s"
                * (MAX_EXCHANGE_BYTES - len(document.model_dump_json().encode()))
            }
        )
        assert len(document.model_dump_json().encode()) == MAX_EXCHANGE_BYTES
        command = ConfigurationDerive(
            document=document, operation_id="boundary", name="Boundary", actor="接收者"
        )
        payload = command.model_dump_json().encode()
        assert MAX_EXCHANGE_BYTES < len(payload) < MAX_DERIVE_REQUEST_BYTES
        payload += b" " * (MAX_DERIVE_REQUEST_BYTES - len(payload))
        response = client.post(
            ROOT + "/derive",
            content=payload,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 200, response.text
        assert (
            runtime.application.configuration_exchange.read(
                document.content_hash
            ).document
            == document
        )
        assert (
            client.post(ROOT + "/derive", json=command.model_dump(mode="json")).json()
            == response.json()
        )


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"transfer-encoding", b"chunked")],
        [(b"content-length", b"1")],
        [(b"content-length", str(MAX_DERIVE_REQUEST_BYTES + 1).encode())],
    ],
    ids=["no-length", "chunked", "false-length", "actual-length"],
)
def test_derive_oversize_stream_stops_before_parsing_or_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, headers: list[tuple[bytes, bytes]]
) -> None:
    with LocalDaemonRuntime(tmp_path) as runtime:
        service = runtime.application.configuration_exchange

        async def unexpected_json(_request: Request) -> object:
            pytest.fail("Oversized input reached JSON parsing")

        def unexpected_derive(_command: ConfigurationDerive) -> None:
            pytest.fail("Oversized input reached the service")

        monkeypatch.setattr(Request, "json", unexpected_json)
        monkeypatch.setattr(service, "derive", unexpected_derive)
        received = 0
        sent: list[Message] = []

        async def receive() -> Message:
            nonlocal received
            received += 1
            # Whitespace crosses the budget before even invalid JSON is parsed.
            # A further unread tail proves we stop consuming on the first excess.
            assert received <= 33, "Read beyond the first oversized chunk"
            return {
                "type": "http.request",
                "body": b" " * (1024 * 1024) if received <= 32 else b" ",
                "more_body": True,
            }

        async def send(message: Message) -> None:
            sent.append(message)

        asyncio.run(
            runtime.app()(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "POST",
                    "scheme": "http",
                    "path": ROOT + "/derive",
                    "raw_path": (ROOT + "/derive").encode(),
                    "query_string": b"",
                    "root_path": "",
                    "headers": [
                        (b"host", b"testserver"),
                        (b"content-type", b"application/json"),
                        *headers,
                    ],
                    "client": ("testclient", 50000),
                    "server": ("testserver", 80),
                },
                receive,
                send,
            )
        )
        assert received == 33
        assert sent[0]["status"] == 413
        assert json.loads(sent[1]["body"])["detail"] == (
            "Configuration derive request exceeds the 32 MiB limit"
        )
        with service._store.sqlite.read_transaction() as connection:
            counts = connection.execute(
                "SELECT COUNT(*) FROM configuration_imports UNION ALL "
                "SELECT COUNT(*) FROM configuration_derivations UNION ALL "
                "SELECT COUNT(*) FROM parameter_revisions UNION ALL "
                "SELECT COUNT(*) FROM parameter_branch_commits UNION ALL "
                "SELECT COUNT(*) FROM setup_definitions UNION ALL "
                "SELECT COUNT(*) FROM setup_revisions"
            ).fetchall()
            assert [row[0] for row in counts] == [0] * 6


@pytest.mark.parametrize(
    ("payload", "error_type", "location"),
    [(b"{", "json_invalid", ["body", 1]), (b"{}", "missing", ["body", "document"])],
)
def test_derive_preserves_fastapi_validation(
    tmp_path: Path, payload: bytes, error_type: str, location: list[str | int]
) -> None:
    with LocalDaemonRuntime(tmp_path) as runtime, TestClient(runtime.app()) as client:
        response = client.post(
            ROOT + "/derive",
            content=payload,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422
        assert response.json()["detail"][0]["type"] == error_type
        assert response.json()["detail"][0]["loc"] == location
        schema = client.get("/openapi.json").json()
        operation = schema["paths"][ROOT + "/derive"]["post"]
        assert operation["requestBody"] == {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ConfigurationDerive"}
                }
            },
        }
        assert "422" in operation["responses"]
        assert runtime.application.configuration_exchange.list() == ()


def test_derive_retains_independent_document_limit(tmp_path: Path) -> None:
    with LocalDaemonRuntime(tmp_path) as runtime, TestClient(runtime.app()) as client:
        document = seed(runtime).model_copy(
            update={"origin_store": "s" * MAX_EXCHANGE_BYTES}
        )
        command = ConfigurationDerive(
            document=document,
            operation_id="oversize-document",
            name="Too big",
            actor="receiver",
        )
        payload = command.model_dump_json().encode()
        assert len(payload) < MAX_DERIVE_REQUEST_BYTES
        response = client.post(
            ROOT + "/derive",
            content=payload,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422
        assert (
            response.json()["detail"]
            == "Configuration exchange exceeds the 16 MiB limit"
        )
        assert runtime.application.configuration_exchange.list() == ()
        assert len(runtime.application.config.parameter_revisions()) == 1


def test_no_run_export_inspect_cancel_derive_edit_and_reopen(tmp_path: Path) -> None:
    with LocalDaemonRuntime(tmp_path / "sender") as sender:
        original = seed(sender)
        schema_only = sender.application.configuration_exchange.export(
            ConfigurationExport(
                parameter_revision="starting-values",
                label="Schema",
                include_values=False,
            )
        )
        assert schema_only.catalog == original.catalog
        assert not schema_only.parameters.values
    location = tmp_path / "receiver"
    with LocalDaemonRuntime(location) as receiver, TestClient(receiver.app()) as client:
        body = original.model_dump(mode="json")
        assert client.post(ROOT + "/inspect", json=body).status_code == 200
        file = client.post(ROOT + "/file", json=body)
        assert file.status_code == 200
        assert file.json() == body
        assert "configuration.json" in file.headers["content-disposition"]
        assert client.get(ROOT + "/imports").json() == []
        assert receiver.application.config.parameter_revisions() == ()
        command = ConfigurationDerive(
            document=original, name="My inputs", actor="receiver", operation_id="first"
        )
        response = client.post(ROOT + "/derive", json=command.model_dump(mode="json"))
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert (
            client.post(ROOT + "/derive", json=command.model_dump(mode="json")).json()
            == receipt
        )
        assert receipt["branch"]["publication"] is None
        saved = client.get("/api/v1/parameters/revisions").json()["items"][0]
        assert saved["id"] == "My inputs"
        # The existing editor's ordinary save + branch CAS, not a new exchange editor.
        updated = client.post(
            "/api/v1/parameters/branch-commits",
            json={
                "name": "My inputs",
                "expected_generation": 1,
                "actor": "receiver",
                "source": {
                    "revision_id": "edited",
                    "catalog": saved["catalog"],
                    "parameters": saved["parameters"],
                    "actor": "receiver",
                },
            },
        )
        assert updated.status_code == 200
        assert (
            client.post(ROOT + "/derive", json=command.model_dump(mode="json")).json()
            == receipt
        )
        another = command.model_copy(update={"operation_id": "second"})
        assert (
            client.post(
                ROOT + "/derive", json=another.model_dump(mode="json")
            ).status_code
            == 409
        )
        another = another.model_copy(update={"name": "Another copy"})
        assert (
            client.post(
                ROOT + "/derive", json=another.model_dump(mode="json")
            ).status_code
            == 200
        )
        assert (
            client.get(ROOT + f"/imports/{original.content_hash}").json()["document"]
            == body
        )
    with LocalDaemonRuntime(location) as reopened:
        assert (
            reopened.application.configuration_exchange.read(
                original.content_hash
            ).document
            == original
        )
        assert (
            len(reopened.application.configuration_exchange.list()[0].derivations) == 2
        )


def test_source_is_verified_inert_and_explicitly_downloaded(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_author_scaffold(source)
    (source / "src/never_execute.py").write_text(
        'raise RuntimeError("never execute imported code")\n'
    )
    for relative in ("scopecat.runtime.toml", "src/scopecat.runtime.toml"):
        (source / relative).write_text(
            '[runtime]\ndata_root = "/machine/private/application-data"\n'
        )
    bundle = capture_sources(open_project(source, resolve_adapter=False))
    assert not any(name.endswith("scopecat.runtime.toml") for name in bundle.files)
    with (
        LocalDaemonRuntime(tmp_path / "app") as runtime,
        TestClient(runtime.app()) as client,
    ):
        original = seed(runtime).model_copy(update={"source": bundle})
        inspected = client.post(
            ROOT + "/inspect", json=original.model_dump(mode="json")
        )
        assert inspected.status_code == 200
        assert "src/never_execute.py" in inspected.json()["source_files"]
        assert "not been accepted" in " ".join(inspected.json()["notices"])
        kept = client.post(ROOT + "/imports", json=original.model_dump(mode="json"))
        assert kept.status_code == 200
        path = ROOT + f"/imports/{original.content_hash}/source"
        assert client.post(path).status_code == 422
        accepted = client.post(path + "?accepted=true")
        assert accepted.status_code == 200
        with ZipFile(io.BytesIO(accepted.content)) as archive:
            assert not any(
                name.endswith("scopecat.runtime.toml") for name in archive.namelist()
            )
            assert all(
                b"/machine/private/application-data" not in archive.read(name)
                for name in archive.namelist()
            )
            assert archive.read("src/never_execute.py").startswith(
                b"raise RuntimeError"
            )
        assert runtime.application.setup.list() == ()
        invalid = original.model_dump(mode="json")
        invalid["source"]["files"]["src/never_execute.py"] = "ZmFrZQ=="
        assert client.post(ROOT + "/inspect", json=invalid).status_code == 422
        assert len(runtime.application.configuration_exchange.list()) == 1


def test_setup_mapping_is_explicit_and_failure_rolls_back_every_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with (
        LocalDaemonRuntime(
            tmp_path,
            bootstrap_config=load_config(),
            instrument_endpoint=signal_endpoint(),
        ) as runtime,
        TestClient(runtime.app()) as client,
    ):
        original = seed(runtime).model_copy(
            update={"setup": runtime.application.setup.definition("initial")}
        )
        command = ConfigurationDerive(
            document=original,
            name="My experiment",
            actor="receiver",
            operation_id="setup",
            include_setup=True,
        )
        # Even though sender IDs happen to exist locally, do not infer consent.
        failed = client.post(ROOT + "/derive", json=command.model_dump(mode="json"))
        assert failed.status_code == 422
        assert runtime.application.configuration_exchange.list() == ()
        assert "My experiment" not in {
            x.id for x in runtime.application.config.parameter_revisions()
        }
        devices = runtime.application.devices.list()
        assert original.setup is not None
        mappings = {
            item.id: next(
                x.device.head for x in devices if x.device.id == item.device_id
            )
            for item in original.setup.definition.instruments
        }
        command = command.model_copy(update={"bindings": mappings})
        # Fail after parameters and setup have been written but before receipt commit.
        service = runtime.application.configuration_exchange
        original_save = runtime.application.setup.save_in_transaction

        def fail_after_setup(
            connection: sqlite3.Connection, command: SetupSaveCommand
        ) -> None:
            original_save(connection, command)
            raise ValueError("injected after setup")

        with monkeypatch.context() as patch:
            patch.setattr(
                runtime.application.setup, "save_in_transaction", fail_after_setup
            )
            assert (
                client.post(
                    ROOT + "/derive", json=command.model_dump(mode="json")
                ).status_code
                == 422
            )
        assert service.list() == ()
        assert "My experiment setup" not in {
            x.id for x in runtime.application.setup.definitions()
        }
        result = client.post(ROOT + "/derive", json=command.model_dump(mode="json"))
        assert result.status_code == 200, result.text
        assert result.json()["setup_pending"] is False
        assert (
            result.json()["setup"]["resolution"]["definition_id"]
            == "My experiment setup"
        )
        assert (
            runtime.application.config.parameter_branch("My experiment").publication
            is None
        )


def test_schema_only_partial_copy_and_invalid_schema_are_explicit(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path) as runtime, TestClient(runtime.app()) as client:
        original = seed(runtime)
        invalid = original.model_dump(mode="json")
        invalid["catalog"]["definitions"] = []
        assert client.post(ROOT + "/inspect", json=invalid).status_code == 422
        assert runtime.application.configuration_exchange.list() == ()
        document = runtime.application.configuration_exchange.export(
            ConfigurationExport(
                parameter_revision="starting-values",
                label="Only schema",
                include_values=False,
            )
        )
        command = ConfigurationDerive(
            document=document,
            name="Unknown inputs",
            actor="receiver",
            operation_id="schema-only",
        )
        receipt = runtime.application.configuration_exchange.derive(command)
        assert receipt.branch.publication is None
        assert not runtime.application.config.parameter_revision(
            "Unknown inputs"
        ).parameters.values
        changed = command.model_copy(update={"name": "Changed intent"})
        assert (
            client.post(
                ROOT + "/derive", json=changed.model_dump(mode="json")
            ).status_code
            == 409
        )
        assert len(runtime.application.configuration_exchange.list()) == 1


def test_current_snapshot_preserves_original_receipt_and_editable_copy(
    tmp_path: Path,
) -> None:
    from scopecat_server.snapshots import create_snapshot, restore_snapshot

    source = tmp_path / "project"
    source.mkdir()
    (source / "scopecat.toml").write_text("[lab]\n")
    with LocalDaemonRuntime(source) as runtime:
        original = seed(runtime)
        receipt = runtime.application.configuration_exchange.derive(
            ConfigurationDerive(
                document=original,
                name="Saved copy",
                actor="receiver",
                operation_id="snapshot",
            )
        )
    create_snapshot(open_project(source, resolve_adapter=False), tmp_path / "snapshot")
    restore_snapshot(tmp_path / "snapshot", tmp_path / "restored")
    with LocalDaemonRuntime(tmp_path / "restored") as runtime:
        assert (
            runtime.application.configuration_exchange.read(
                original.content_hash
            ).document
            == original
        )
        assert runtime.application.configuration_exchange.list()[0].derivations == (
            receipt,
        )
        assert (
            runtime.application.config.parameter_revision("Saved copy").parameters
            == original.parameters
        )
