"""Application-owned exercises using the common scientific and task services."""

import shutil
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from threading import Lock
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZIP_DEFLATED, ZipFile

from scopecat.config.resolution import compose_configuration
from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.kernel.entity import EntityRef
from scopecat.records.config import RoutingGraph, Topology
from scopecat.records.configuration_fence import SetupRevisionFence
from scopecat.records.data_cleanup import DataCleanupCommand, DataCleanupSelection
from scopecat.records.execution_scenario import SoftwareExecutionScenario
from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot
from scopecat.records.parameter_revision import (
    ParameterRevision,
    parameter_revision_hash,
)
from scopecat.records.practice import (
    PracticeCatalog,
    PracticeClearCommand,
    PracticeCreateCommand,
    PracticeScope,
)
from scopecat.records.setup import SetupDefinition, SetupDefinitionRevision

from scopecat_server.services.automation import AutomationService
from scopecat_server.services.data_cleanup import DataCleanupService
from scopecat_server.storage.sqlite.parameter_revisions import (
    ParameterRevisionRepository,
)
from scopecat_server.storage.sqlite.practice import PracticeOwnership
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository


class PracticeService:
    def __init__(
        self,
        store: SQLiteProjectStore,
        automation: AutomationService,
        cleanup: DataCleanupService,
    ):
        self.store = store
        self.automation = automation
        self.cleanup = cleanup
        self.directory = store.objects.root.parent / "practice"
        self._lifecycle_lock = Lock()

    def catalog(self) -> PracticeCatalog:
        with self.store.sqlite.read_transaction() as connection:
            return PracticeCatalog(
                items=tuple(
                    self._located(item) for item in PracticeOwnership(connection).list()
                )
            )

    def get(self, scope_id: str) -> PracticeScope:
        with self.store.sqlite.read_transaction() as connection:
            return self._located(PracticeOwnership(connection).get(scope_id))

    def _located(self, scope: PracticeScope) -> PracticeScope:
        return scope.model_copy(update={"directory": str(self.directory / scope.id)})

    def create(self, command: PracticeCreateCommand) -> PracticeScope:
        with self._lifecycle_lock:
            return self._create(command)

    def _create(self, command: PracticeCreateCommand) -> PracticeScope:
        from scopecat_server.practice_lesson import PeakPracticeIntent, manual_peaks

        identity = uuid5(NAMESPACE_URL, f"scopecat-practice:{command.request_key}").hex
        with self.store.sqlite.write_transaction() as connection:
            owners = PracticeOwnership(connection)
            try:
                existing = self._located(owners.get(identity))
            except KeyError:
                pass
            else:
                if existing.state == "active":
                    self._write_files(existing)
                return existing
            scope = PracticeScope(
                id=identity,
                lesson=command.lesson,
                title="Scan and choose a peak",
                directory=str(self.directory / identity),
            )
            owners.save(scope)
            setups = SQLiteSetupRepository(connection)
            definition = SetupDefinitionRevision(
                id=f"practice:{identity}",
                actor="practice",
                definition=SetupDefinition(
                    topology=Topology(
                        entities=[EntityRef(id="subject", kind="logical_subject")]
                    ),
                    instruments=(),
                    routing=RoutingGraph(routes=[]),
                    domain_target=None,
                    scenario=SoftwareExecutionScenario(
                        id=f"practice:{identity}",
                        label="Synthetic peak practice",
                        model_id="scopecat.practice.peak",
                        model_version="1",
                        capabilities=("Synthetic response", "Manual frequency choice"),
                        limitations=("No physical device access or calibration claim",),
                    ),
                ),
            )
            _ = setups.save_definition(definition)
            owners.claim(identity, "setup_definition", definition.id)
            setup = setups.resolve(definition.id)
            owners.claim(identity, "setup", setup.id)
            catalog = ParameterCatalog(id=f"practice:{identity}")
            values = ParameterSnapshot(id=f"practice:{identity}")
            parameters = ParameterRevision(
                id=f"practice:{identity}",
                catalog=catalog,
                parameters=values,
                content_hash=parameter_revision_hash(catalog, values),
                actor="practice",
            )
            _ = ParameterRevisionRepository(connection).save(parameters)
            owners.claim(identity, "parameters", parameters.id)
            config = compose_configuration(
                setup.setup,
                id=f"practice:{identity}",
                system_id=f"practice:{identity}",
                catalog=catalog,
                parameters=values,
            )
            intent = PeakPracticeIntent(
                scope_id=identity, config=config, setup=setup.ref
            )
            run = self.automation.submit_in_transaction(
                connection,
                definition=manual_peaks.ref,
                request_key=f"practice:{identity}",
                intent=intent.model_dump(mode="json"),
                scientific_binding=bind_scientific_evidence(
                    catalog_id=self.store.identity(),
                    config=config,
                    samples=(),
                    sample_revisions={},
                ),
                expected_configuration=SetupRevisionFence(revision=setup.ref),
            )
            scope = scope.model_copy(update={"procedure_id": run.procedure_run_id})
            owners.save(scope)
        self._write_files(scope)
        return scope

    def clear(
        self,
        scope_id: str,
        command: PracticeClearCommand,
        *,
        retire: Callable[[str], None],
    ) -> PracticeScope:
        with self._lifecycle_lock:
            with self.store.sqlite.write_transaction() as connection:
                owners = PracticeOwnership(connection)
                scope = self._located(owners.get(scope_id))
                if scope.state == "cleared":
                    return scope
                if (
                    scope.file_disposition is not None
                    and scope.file_disposition != command.files
                ):
                    raise ValueError(
                        "Resume cleanup with the previously selected file choice"
                    )
                scope = scope.model_copy(
                    update={
                        "state": "cleaning",
                        "file_disposition": command.files,
                        "cleanup_error": None,
                    }
                )
                owners.save(scope)
                procedures = owners.resources(scope_id, "procedure")
            try:
                for procedure in procedures:
                    retire(procedure)
                scope = scope.model_copy(update={"workers_retired": True})
                with self.store.sqlite.write_transaction() as connection:
                    PracticeOwnership(connection).save(scope)
                with self.store.sqlite.read_transaction() as connection:
                    owners = PracticeOwnership(connection)
                    selection = DataCleanupSelection(
                        runs=owners.resources(scope_id, "run"),
                        analyses=owners.resources(scope_id, "analysis"),
                        procedures=owners.resources(scope_id, "procedure"),
                        setups=owners.resources(scope_id, "setup"),
                        setup_definitions=owners.resources(
                            scope_id, "setup_definition"
                        ),
                        parameters=owners.resources(scope_id, "parameters"),
                    )
                operation = self.cleanup.execute(
                    DataCleanupCommand(
                        request_key=f"practice:{scope_id}",
                        preview=self.cleanup.preview(selection),
                    ),
                    settle=retire,
                )
                if operation.state != "complete":
                    raise ValueError(
                        operation.error or "Data cleanup remains unfinished"
                    )
                directory = self.directory / scope.id
                if command.files == "discard" and directory.exists():
                    shutil.rmtree(directory)
                scope = scope.model_copy(update={"state": "cleared"})
            except Exception as error:
                scope = scope.model_copy(update={"cleanup_error": str(error)})
                with self.store.sqlite.write_transaction() as connection:
                    PracticeOwnership(connection).save(scope)
                raise
            with self.store.sqlite.write_transaction() as connection:
                PracticeOwnership(connection).save(scope)
            return scope

    def _write_files(self, scope: PracticeScope) -> None:
        directory = Path(scope.directory)
        directory.mkdir(parents=True, exist_ok=True)
        files = {
            "README.md": (
                "# Scan and choose a peak\n\n"
                "Continue this practice from Help in Scopecat. Read its curve in "
                "Decisions, record a frequency, then explicitly continue the task. "
                "Reopening does not repeat the scan.\n\n"
                "This folder is for your notes; open it directly in VS Code. "
                "No notebook kernel, separate environment or server is needed. "
                "Clearing practice keeps these files unless you choose to delete "
                "them. Export a copy from Help when needed.\n"
            ),
            "notes.md": "# My observations\n\n",
        }
        for name, contents in files.items():
            path = directory / name
            if not path.exists():
                _ = path.write_text(contents, encoding="utf-8")

    def export_files(self, scope_id: str) -> bytes:
        scope = self.get(scope_id)
        directory = self.directory / scope.id
        if directory.is_symlink():
            raise ValueError(
                "Practice folder was replaced by a link; export its files manually"
            )
        content = BytesIO()
        with ZipFile(content, "w", compression=ZIP_DEFLATED) as archive:
            for parent, directories, files in directory.walk():
                directories[:] = [
                    name for name in directories if not (parent / name).is_symlink()
                ]
                for name in files:
                    path = parent / name
                    if not path.is_symlink():
                        archive.write(path, path.relative_to(directory))
        return content.getvalue()
