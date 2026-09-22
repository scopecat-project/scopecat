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
from scopecat.records.parameter_revision import (
    ParameterRevision,
    parameter_revision_hash,
)
from scopecat.records.scientific_selection import ParameterConfiguration
from scopecat.records.setup import ExecutableSetupSnapshot
from scopecat_testkit.config_registry import load_config
from scopecat_testkit.server.runtime import SQLiteTestRunRepository

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.instruments.actors import InstrumentActorRegistry
from scopecat_server.services.setup import SetupService
from scopecat_server.storage.sqlite.calibration_cohorts import (
    SQLiteCalibrationCohortStore,
)
from scopecat_server.storage.sqlite.config_registry import SQLiteConfigRegistryStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane
from scopecat_server.storage.sqlite.parameter_revisions import (
    ParameterRevisionRepository,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


def services(root: Path) -> tuple[SetupService, SQLiteConfigRegistryStore]:
    database = SQLiteDatabase(root / "control.sqlite3")
    objects = root / "objects"
    SQLiteProjectStore(database, objects).bootstrap()
    registry = SQLiteConfigRegistryStore(
        database, runs=SQLiteTestRunRepository(database, objects)
    )
    config = load_config()
    template = ConfigurationTemplate(
        id="bench",
        label="Software bench",
        setup=ExecutableSetupSnapshot.from_config(config),
        catalog=config.parameter_catalog,
        parameters=config.parameter_snapshot,
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
        revision_id="trial",
        actor="operator",
    )
    first = service.import_template(command)
    assert first.parameters.parameters == template.parameters
    assert first.parameters.catalog == template.catalog
    assert isinstance(first.selection.configuration, ParameterConfiguration)
    assert first.selection.configuration.ref == first.parameters.ref
    assert first.selection.configuration.setup == first.setup.ref
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


def test_failed_parameter_import_rolls_back_setup(tmp_path: Path) -> None:
    service, _ = services(tmp_path)
    template = service.templates()[0]
    database = SQLiteDatabase(tmp_path / "control.sqlite3")
    with SQLiteControlPlane(database).write_transaction() as connection:
        ParameterRevisionRepository(connection).save(
            ParameterRevision(
                id="taken",
                catalog=template.catalog,
                parameters=template.parameters,
                content_hash=parameter_revision_hash(
                    template.catalog, template.parameters
                ),
                actor="maintainer",
            )
        )
    before = service.list()
    with pytest.raises(BackendConflict, match="different content or provenance"):
        service.import_template(
            ConfigurationTemplateImportCommand(
                template_id=template.id,
                content_hash=template.content_hash,
                revision_id="taken",
                actor="operator",
            )
        )
    assert service.list() == before


def test_empty_lab_import_saves_only_independent_revisions(tmp_path: Path) -> None:
    service, _ = services(tmp_path)
    template = service.templates()[0]
    command = ConfigurationTemplateImportCommand(
        template_id=template.id,
        content_hash=template.content_hash,
        revision_id="first",
        actor="operator",
    )
    result = service.import_template(command)
    reopened, _ = services(tmp_path)
    assert reopened.import_template(command) == result
    database = SQLiteDatabase(tmp_path / "control.sqlite3")
    with SQLiteControlPlane(database).read_transaction() as connection:
        assert ParameterRevisionRepository(connection).get("first") == result.parameters
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM config_registry_entries"
            ).fetchone()[0]
            == 0
        )
    with pytest.raises(BackendNotFound, match="no executable setup"):
        service.current()
