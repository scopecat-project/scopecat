"""Adapter recipes reuse setup/config transactions without selecting defaults."""

from pathlib import Path

import pytest
from scopecat.config.registry.service import (
    ConfigRevision,
    DirectConfigRevisionSource,
    load_active_config_registry_snapshot,
    publish_config_revision,
)
from scopecat.daemon.wire import ConfigurationTemplateImportCommand
from scopecat.records.configuration_template import ConfigurationTemplate
from scopecat.records.scientific_selection import SavedConfiguration
from scopecat_testkit.config_registry import load_config
from scopecat_testkit.server.runtime import SQLiteTestRunRepository

from scopecat_server.errors import BackendConflict
from scopecat_server.instruments.actors import InstrumentActorRegistry
from scopecat_server.services.setup import SetupService
from scopecat_server.storage.sqlite.calibration_cohorts import (
    SQLiteCalibrationCohortStore,
)
from scopecat_server.storage.sqlite.config_registry import SQLiteConfigRegistryStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


def services(root: Path) -> tuple[SetupService, SQLiteConfigRegistryStore]:
    database = SQLiteDatabase(root / "control.sqlite3")
    objects = root / "objects"
    SQLiteProjectStore(database, objects).bootstrap()
    registry = SQLiteConfigRegistryStore(
        database, runs=SQLiteTestRunRepository(database, objects)
    )
    template = ConfigurationTemplate(
        id="bench", label="Software bench", config=load_config()
    )
    service = SetupService(
        control=SQLiteControlPlane(database),
        config_registry=registry,
        actors=InstrumentActorRegistry(),
        calibration_cohorts=SQLiteCalibrationCohortStore(database),
        templates=(template,),
    )
    return service, registry


def test_import_retries_and_reopens_without_changing_authority(tmp_path: Path) -> None:
    service, registry = services(tmp_path)
    publish_config_revision(
        revision=ConfigRevision(
            entry_id="default",
            actor="maintainer",
            source=DirectConfigRevisionSource(config=load_config()),
        ),
        expected_generation=0,
        unit_of_work=registry.write_unit_of_work,
    )
    current = service.current()
    template = service.templates()[0]
    command = ConfigurationTemplateImportCommand(
        template_id=template.id,
        content_hash=template.content_hash,
        entry_id="trial",
        actor="operator",
    )
    first = service.import_template(command)
    assert first.configuration.config == template.config
    assert isinstance(first.selection.configuration, SavedConfiguration)
    assert first.selection.configuration.ref.entry_id == "trial"
    assert service.import_template(command) == first
    assert service.current() == current
    assert (
        load_active_config_registry_snapshot(
            unit_of_work=registry.write_unit_of_work
        ).entry.id
        == "default"
    )
    reopened, _ = services(tmp_path)
    assert reopened.import_template(command) == first
    for changed in ({"actor": "another"}, {"note": "another"}):
        with pytest.raises(BackendConflict, match="different"):
            service.import_template(command.model_copy(update=changed))
    with pytest.raises(BackendConflict, match="changed; review"):
        service.import_template(
            command.model_copy(update={"content_hash": "sha256:" + "a" * 64})
        )


def test_failed_config_import_rolls_back_setup(tmp_path: Path) -> None:
    service, registry = services(tmp_path)
    publish_config_revision(
        revision=ConfigRevision(
            entry_id="taken",
            actor="maintainer",
            source=DirectConfigRevisionSource(config=load_config()),
        ),
        expected_generation=0,
        unit_of_work=registry.write_unit_of_work,
    )
    before = service.list()
    template = service.templates()[0]
    with pytest.raises(BackendConflict, match="committed differently"):
        service.import_template(
            ConfigurationTemplateImportCommand(
                template_id=template.id,
                content_hash=template.content_hash,
                entry_id="taken",
                actor="operator",
            )
        )
    assert service.list() == before
