"""Maintained executable setup selection and device-safe authority changes."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager

from scopecat.config.resolution import compose_configuration, validate_config_profile
from scopecat.daemon.wire import (
    ConfigurationTemplateImportCommand,
    ConfigurationTemplateImportResult,
    ConfigurationTemplateView,
    SetupImportCommand,
    SetupSaveCommand,
)
from scopecat.kernel.errors import CheckFailed, Conflict, DataIntegrityError, NotFound
from scopecat.records.configuration_template import ConfigurationTemplate
from scopecat.records.device import (
    DeviceConnection,
    DeviceConnectionRevision,
    DeviceSafetyPolicy,
    connection_access_alias,
)
from scopecat.records.parameter_revision import (
    ParameterRevision,
    ParameterRevisionContent,
    parameter_revision_hash,
)
from scopecat.records.setup import (
    ExecutableSetupSnapshot,
    SetupDefinition,
    SetupDefinitionRevision,
    SetupInstrumentBinding,
    SetupRevision,
    SetupRevisionRef,
)

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.services.devices import DeviceService
from scopecat_server.setup_access import setup_config
from scopecat_server.storage.sqlite.config_registry import SQLiteConfigRegistryStore
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane
from scopecat_server.storage.sqlite.devices import DeviceRepository
from scopecat_server.storage.sqlite.parameter_revisions import (
    ParameterRevisionRepository,
)
from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository


class SetupService:
    def __init__(
        self,
        *,
        control: SQLiteControlPlane,
        config_registry: SQLiteConfigRegistryStore,
        devices: DeviceService,
        templates: tuple[ConfigurationTemplate, ...] = (),
    ) -> None:
        self._control = control
        self._registry = config_registry
        self._devices = devices

        self.initialize_templates(templates)

    def initialize_templates(
        self, templates: tuple[ConfigurationTemplate, ...]
    ) -> None:
        """Capture adapter recipes during startup, before serving any requests."""
        if len({template.id for template in templates}) != len(templates):
            raise ValueError("configuration template IDs must be unique")
        self._templates = {
            template.id: template.model_copy(deep=True) for template in templates
        }

    def initialize(
        self,
        factory: Callable[
            [], tuple[ExecutableSetupSnapshot | None, ParameterRevisionContent | None]
        ],
    ) -> None:
        """Seed independent inputs once, atomically; never select global defaults."""
        with self._errors(), self._control.write_transaction() as connection:
            if (
                connection.execute(
                    "SELECT id FROM application_initialization WHERE id = 1"
                ).fetchone()
                is not None
            ):
                return
            equipment, parameters = factory()
            if equipment is None and parameters is not None:
                raise ValueError("initial parameters require a setup declaration")
            if equipment is not None:
                self._import_recipe(
                    connection,
                    SetupImportCommand(
                        revision_id="initial",
                        setup=equipment,
                        actor="scopecat",
                        note="Initial application setup",
                    ),
                )
            if parameters is not None:
                assert equipment is not None
                compose_configuration(
                    equipment,
                    id=parameters.id,
                    system_id=parameters.system_id,
                    catalog=parameters.catalog,
                    parameters=parameters.parameters,
                )
                ParameterRevisionRepository(connection).save(
                    ParameterRevision(
                        id=parameters.id,
                        catalog=parameters.catalog,
                        parameters=parameters.parameters,
                        content_hash=parameter_revision_hash(
                            parameters.catalog, parameters.parameters
                        ),
                        actor="scopecat",
                        note="Initial application parameters",
                    )
                )
            connection.execute("INSERT INTO application_initialization(id) VALUES (1)")

    def templates(self) -> tuple[ConfigurationTemplateView, ...]:
        return tuple(
            ConfigurationTemplateView.from_template(template)
            for template in self._templates.values()
        )

    def import_template(
        self, command: ConfigurationTemplateImportCommand
    ) -> ConfigurationTemplateImportResult:
        with self._errors():
            template = self._templates.get(command.template_id)
            if template is None:
                raise BackendNotFound(
                    "configuration template is not provided by this adapter"
                )
            if template.content_hash != command.content_hash:
                raise BackendConflict("configuration template changed; review it again")
            setup = template.setup
            compose_configuration(
                setup,
                id=command.revision_id,
                system_id="template",
                catalog=template.catalog,
                parameters=template.parameters,
            )
            note = f"Template {template.id} ({template.content_hash})\n{command.note}"
            imported = SetupImportCommand(
                revision_id=f"template-setup:{command.revision_id}",
                setup=setup,
                actor=command.actor,
                note=note,
            )
            with self._control.write_transaction() as connection:
                retained = self._import_recipe(connection, imported)
                parameters = ParameterRevisionRepository(connection).save(
                    ParameterRevision(
                        id=command.revision_id,
                        actor=command.actor,
                        note=note,
                        catalog=template.catalog,
                        parameters=template.parameters,
                        content_hash=parameter_revision_hash(
                            template.catalog, template.parameters
                        ),
                    ),
                )
            return ConfigurationTemplateImportResult(
                setup=retained,
                parameters=parameters,
            )

    def get(self, revision_id: str) -> SetupRevision:
        with self._errors(), self._registry.read_unit_of_work() as work:
            return work.setups.read_revision(revision_id)

    def require_available(self, ref: SetupRevisionRef) -> SetupRevision:
        with self._errors(), self._control.read_transaction() as connection:
            revision = SQLiteSetupRepository(connection).read_revision(ref.revision_id)
            if revision.ref != ref:
                raise BackendConflict("setup reference differs from retained content")
            DeviceRepository(connection).require_current(revision.resolution.devices)
            self._validate_resolution(connection, revision)
            return revision

    def list(self) -> tuple[SetupRevision, ...]:
        with self._errors(), self._registry.read_unit_of_work() as work:
            return work.setups.list_revisions()

    def save(self, command: SetupSaveCommand) -> SetupRevision:
        with self._control.write_transaction() as connection:
            return self.save_in_transaction(connection, command)

    def save_in_transaction(
        self, connection: sqlite3.Connection, command: SetupSaveCommand
    ) -> SetupRevision:
        """Save through existing validation in an owning atomic transaction."""
        definition = SetupDefinitionRevision(
            id=command.revision_id,
            definition=command.setup,
            actor=command.actor,
            note=command.note,
        )
        with self._errors(), self._registry.borrowed_unit_of_work(connection) as work:
            work.setups.save_definition(definition)
            revision = work.setups.resolve(definition.id)
            self._validate_resolution(connection, revision)
            validate_config_profile(setup_config(revision))
            return revision

    def definitions(self) -> tuple[SetupDefinitionRevision, ...]:
        with self._control.read_transaction() as connection:
            return SQLiteSetupRepository(connection).definitions()

    def definition(self, definition_id: str) -> SetupDefinitionRevision:
        with self._errors(), self._control.read_transaction() as connection:
            return SQLiteSetupRepository(connection).definition(definition_id)

    def resolve(self, definition_id: str) -> SetupRevision:
        with self._errors(), self._control.write_transaction() as connection:
            revision = SQLiteSetupRepository(connection).resolve(definition_id)
            self._validate_resolution(connection, revision)
            return revision

    def _validate_resolution(
        self, connection: sqlite3.Connection, revision: SetupRevision
    ) -> None:
        devices = DeviceRepository(connection)
        for ref in revision.resolution.devices:
            self._devices.require_driver(
                devices.revision(ref.revision_id).content.driver
            )

    def import_recipe(self, command: SetupImportCommand) -> SetupRevision:
        with self._errors(), self._control.write_transaction() as connection:
            return self._import_recipe(connection, command)

    def _import_recipe(
        self, connection: sqlite3.Connection, command: SetupImportCommand
    ) -> SetupRevision:
        devices = DeviceRepository(connection)
        bindings: list[SetupInstrumentBinding] = []
        for instrument in command.setup.instrument_registry.instruments:
            declared = f"recipe:{instrument.exclusivity_key}"
            address = connection_access_alias(instrument.connection)
            owner = devices.alias_owner(declared)
            if owner is None and address is not None:
                owner = devices.alias_owner(address)
            device_id = owner or instrument.exclusivity_key
            content = DeviceConnection(
                driver=self._devices.driver_ref(instrument.driver_id),
                connection=instrument.connection,
                safety=DeviceSafetyPolicy.from_instrument(instrument),
                access_aliases=(declared,),
            )
            self._devices.validate_connection(content)
            if owner is not None:
                prior = devices.view(owner)
                # Recipes may reference a registered device but are never a second
                # connection editor. An address or driver change needs maintenance.
                if (
                    prior.device.state != "available"
                    or prior.revision.content.model_dump(exclude={"access_aliases"})
                    != content.model_dump(exclude={"access_aliases"})
                ):
                    raise ValueError(
                        f"recipe device {instrument.id} differs from registered device "
                        f"{prior.device.label}; review Devices before importing"
                    )
            else:
                devices.save(
                    label=instrument.id,
                    revision=DeviceConnectionRevision(
                        id=f"{device_id}:{content.content_hash.removeprefix('sha256:')}",
                        device_id=device_id,
                        content=content,
                        actor=command.actor,
                        note=command.note,
                    ),
                    expected_head=None,
                )
            bindings.append(
                SetupInstrumentBinding(
                    id=instrument.id,
                    device_id=device_id,
                    default_state=tuple(instrument.default_state),
                    run_start=instrument.run_start,
                    success_action=instrument.success_action,
                    failure_action=instrument.failure_action,
                )
            )
        setups = SQLiteSetupRepository(connection)
        setups.save_definition(
            SetupDefinitionRevision(
                id=command.revision_id,
                definition=SetupDefinition(
                    topology=command.setup.topology,
                    instruments=tuple(bindings),
                    routing=command.setup.routing,
                    domain_target=command.setup.domain_target,
                    scenario=command.setup.scenario,
                ),
                actor=command.actor,
                note=command.note,
            )
        )
        revision = setups.resolve(command.revision_id)
        validate_config_profile(setup_config(revision))
        return revision

    @staticmethod
    @contextmanager
    def _errors() -> Generator[None]:
        try:
            yield
        except (NotFound, KeyError) as error:
            raise BackendNotFound(str(error)) from error
        except (
            CheckFailed,
            Conflict,
            DataIntegrityError,
            ValueError,
        ) as error:
            raise BackendConflict(str(error)) from error
