"""Installed, headless sharing journey; native dialog clicks are separate evidence.

Run with the packaged Python, passing a fresh work directory and its payload.
Only generated analytic source, new data homes and real local files are used.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast
from zipfile import ZipFile

import httpx2
from pydantic import BaseModel, TypeAdapter

from lab_tools.application_runtime import ApplicationRuntime
from lab_tools.author_environment import prepare_execution_environment
from lab_tools.desktop import DesktopAPI
from lab_tools.desktop_files import save_configuration, save_configuration_source
from lab_tools.desktop_session import DesktopSession
from scopecat.application.launch import LaunchCatalog, LaunchPreview, LaunchSubmission
from scopecat.automation import ProcedureRun
from scopecat.daemon.views import RunSummaryPage
from scopecat.records.configuration_exchange import (
    ConfigurationDerivation,
    ConfigurationDerive,
    ConfigurationExchange,
    ConfigurationExport,
    ConfigurationImportSummary,
)
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.parameter_revision import ParameterRevision
from scopecat.records.scientific_selection import (
    ParameterConfiguration,
    ScientificSelection,
)
from scopecat_server.scaffold import write_author_scaffold  # noqa: TID251

if TYPE_CHECKING:
    import webview


class NavigationStub:
    def run_js(self, _script: str) -> None:
        pass


SEED = """
import sys
from pathlib import Path
import scopecat as sc
source = Path(sys.argv[1])
sys.path.insert(0, str(source / 'src'))
from scopecat_lab.authored.parameters import initial_parameters
from scopecat_lab.configuration import initial_setup
with sc.open_project(source).connect() as author:
    content = initial_parameters()
    author.parameters.save(name='sharing-initial', catalog=content.parameter_catalog,
                           parameters=content.parameter_snapshot)
    author.setup.import_recipe(initial_setup(), name='sharing-analytic')
"""


def post(client: httpx2.Client, path: str, command: BaseModel) -> httpx2.Response:
    response = client.post(
        "/api/v1/" + path,
        content=command.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    assert response.is_success, response.text
    return response


def verify(home: Path, payload: Path) -> None:
    home.mkdir(parents=True, exist_ok=False)
    sender, receiver = (
        ApplicationRuntime(home / name) for name in ("sender", "receiver")
    )
    for runtime in (sender, receiver):
        runtime.configure(
            python=Path(sys.executable),
            static_dir=payload / "gui",
            delivery_root=payload,
        )
    source = home / "sender-source"
    write_author_scaffold(source)
    manifest = source / "scopecat.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'source_roots = ["src"]', 'source_roots = ["./src/."]'
        ),
        encoding="utf-8",
    )
    (source / "src/scopecat.runtime.toml").write_text(
        '[runtime]\ndata_root = "/machine-only/application-data"\n', encoding="utf-8"
    )
    (source / "pyproject.toml").write_text(
        '[project]\nname = "sharing-acceptance"\nversion = "0.0.0"\n'
        'requires-python = ">=3.14"\ndependencies = []\n',
        encoding="utf-8",
    )
    try:
        python = prepare_execution_environment(sender, source)
        workspace = sender.register_source(source, python=python)
        sender_url = sender.start().base_url
        subprocess.run(  # noqa: S603 - packaged Python, generated device-free source
            [sys.executable, "-I", "-B", "-c", SEED, str(source)], check=True
        )
        with httpx2.Client(base_url=sender_url, trust_env=False, timeout=120) as client:
            # Explicit trusted catalog loading retains source without acquiring data.
            catalog_response = client.get(
                "/api/v1/experiment-launcher",
                headers={"X-Scopecat-Workspace": workspace},
            )
            assert catalog_response.is_success, catalog_response.text
            catalog = LaunchCatalog.model_validate_json(catalog_response.content)
            assert catalog.code_revision is not None
            document = ConfigurationExchange.model_validate_json(
                post(
                    client,
                    "configuration-exchange/export",
                    ConfigurationExport(
                        parameter_revision="sharing-initial",
                        label="Installed sharing",
                        setup_definition="sharing-analytic",
                        workspace=workspace,
                        source_revision=catalog.code_revision.content_hash,
                    ),
                ).content
            )
            assert client.get("/api/v1/runs").json()["items"] == []
        assert document.source is not None
        assert document.source.manifest.source_roots == ("src",)
        assert not any(
            name.endswith("scopecat.runtime.toml") for name in document.source.files
        )
        file = home / "configuration.json"
        save_configuration(sender_url, document.model_dump_json(), file)
        sender.stop()
        receiver_url = receiver.start().base_url
        with httpx2.Client(
            base_url=receiver_url, trust_env=False, timeout=120
        ) as client:
            received = ConfigurationExchange.model_validate_json(file.read_bytes())
            assert received == document
            post(client, "configuration-exchange/inspect", received)
            # Cancelling the inspection is intentionally no write operation.
            assert client.get("/api/v1/configuration-exchange/imports").json() == []
            assert client.get("/api/v1/parameters/revisions").json()["items"] == []
            assert client.get("/api/v1/author-workspaces").json()["items"] == []
            derive = ConfigurationDerive(
                document=received,
                name="My shared inputs",
                actor="sharing-acceptance",
                operation_id="installed-sharing",
                include_setup=True,
            )
            receipt = ConfigurationDerivation.model_validate_json(
                post(client, "configuration-exchange/derive", derive).content
            )
            assert post(
                client, "configuration-exchange/derive", derive
            ).json() == receipt.model_dump(mode="json")
            assert receipt.setup is not None
            assert receipt.branch.publication is None
            saved = ParameterRevision.model_validate_json(
                client.get("/api/v1/parameters/revisions/My shared inputs").content
            ).model_dump(mode="json")
            saved["parameters"]["values"][0]["rows"][0]["scale"] = 2.0
            edited_response = client.post(
                "/api/v1/parameters/revisions",
                json={
                    "revision_id": "My edited inputs",
                    "catalog": saved["catalog"],
                    "parameters": saved["parameters"],
                    "actor": "sharing-acceptance",
                },
            )
            assert edited_response.is_success, edited_response.text
            edited = ParameterRevision.model_validate_json(edited_response.content)
            archive = home / "author-source.zip"
            save_configuration_source(receiver_url, receipt.content_hash, archive)
            assert client.get("/api/v1/author-workspaces").json()["items"] == []
            restored = home / "received-source"
            restored.mkdir()
            with ZipFile(archive) as stream:
                stream.extractall(restored)
            before = {
                p.relative_to(restored): p.read_bytes()
                for p in restored.rglob("*")
                if p.is_file()
            }
            # Same Settings action and real environment; only navigation is stubbed.
            session = DesktopSession(receiver, threading.Event())
            api = DesktopAPI(
                session,
                lambda: cast("webview.Window", cast("object", NavigationStub())),
            )
            api.prepare_author_environment(str(restored))
            assert all(
                (restored / p).read_bytes() == content for p, content in before.items()
            )
            workspace = receiver.source(restored)
            catalog_response = client.get(
                "/api/v1/experiment-launcher",
                headers={"X-Scopecat-Workspace": workspace},
            )
            assert catalog_response.is_success, catalog_response.text
            catalog = LaunchCatalog.model_validate_json(catalog_response.content)
            entry = next(item for item in catalog.entries if item.id == "signal")
            request = LaunchRequest(
                workspace_id=workspace,
                action="preview",
                experiment=entry.id,
                version=entry.version,
                code_revision=catalog.code_revision,
                selection=ScientificSelection(
                    configuration=ParameterConfiguration(
                        ref=edited.ref, setup=receipt.setup.ref
                    )
                ),
                inputs={"center": 0.0},
            )
            preview = LaunchPreview.model_validate_json(
                post(client, "experiment-launcher/preview", request).content
            )
            assert preview.point_count == 1
            assert preview.resources == ()
            submission = LaunchSubmission.model_validate_json(
                post(
                    client,
                    "experiment-launcher/submit",
                    request.model_copy(
                        update={
                            "action": "submit",
                            "request_key": "installed-sharing-run",
                            "reviewed": preview.reviewed,
                            "code_revision": preview.code_revision,
                            "manual_state": preview.manual_state,
                            "expected_request_hash": preview.request_hash,
                        }
                    ),
                ).content
            )
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                procedure = ProcedureRun.model_validate_json(
                    client.get("/api/v1/procedures/" + submission.procedure_id).content
                )
                if procedure.state == "closed":
                    break
                assert procedure.state != "attention_required", procedure
                time.sleep(0.2)
            else:
                raise AssertionError("Analytic procedure did not finish")
            assert (
                procedure.closure is not None
                and procedure.closure.status == "succeeded"
            )
            runs = RunSummaryPage.model_validate_json(
                client.get("/api/v1/runs").content
            ).items
            assert len(runs) == 1
            originals = TypeAdapter(
                tuple[ConfigurationImportSummary, ...]
            ).validate_json(
                client.get("/api/v1/configuration-exchange/imports").content
            )
        receiver.stop()
        receiver = ApplicationRuntime(receiver.home)
        with httpx2.Client(
            base_url=receiver.start().base_url, trust_env=False, timeout=30
        ) as client:
            assert (
                RunSummaryPage.model_validate_json(
                    client.get("/api/v1/runs").content
                ).items
                == runs
            )
            assert (
                TypeAdapter(tuple[ConfigurationImportSummary, ...]).validate_json(
                    client.get("/api/v1/configuration-exchange/imports").content
                )
                == originals
            )
            assert (
                ParameterRevision.model_validate_json(
                    client.get("/api/v1/parameters/revisions/My edited inputs").content
                )
                == edited
            )
            assert receiver.source(restored) == workspace
        (home / "result.json").write_text(
            json.dumps(
                {
                    "software": "passed",
                    "native_dialogs": "not-evaluated",
                    "human": "not-evaluated",
                    "physical": "not-evaluated",
                    "source": "generated device-free analytic starter",
                    "dependencies": "empty declarations; independent environment",
                    "checks": [
                        "no-run export",
                        "real file transfer",
                        "inspection cancellation",
                        "configuration derivation and idempotent retry",
                        "ordinary parameter edit",
                        "inert source archive without local runtime bindings",
                        "Settings environment preparation and registration",
                        "trusted catalog load",
                        "fresh preview and one analytic run",
                        "reopen without reacquisition",
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception as error:
        (home / "result.json").write_text(
            json.dumps({"software": "failed", "error": str(error)}, indent=2) + "\n",
            encoding="utf-8",
        )
        raise
    finally:
        sender.stop()
        receiver.stop()
        for name, runtime in (("sender", sender), ("receiver", receiver)):
            log = runtime.root / ".scopecat/daemon.log"
            if log.is_file():
                shutil.copyfile(log, home / f"{name}-daemon.log")


if __name__ == "__main__":
    verify(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
