"""Maintained executable setup selection and device-safe authority changes."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager, nullcontext
from threading import Lock

from scopecat.config.inventory import (
    InstrumentInventoryRekey,
    InstrumentInventoryRemoval,
    InstrumentInventoryRenameRekey,
)
from scopecat.config.registry import service as config_registry_service
from scopecat.config.resolution import compose_configuration, validate_config_profile
from scopecat.control.models import (
    DurableEventInput,
    InventoryMigrationBlocker,
    ResourceKey,
)
from scopecat.daemon.wire import (
    ConfigurationTemplateImportCommand,
    ConfigurationTemplateImportResult,
    ConfigurationTemplateView,
    SetupActivateCommand,
    SetupImportCommand,
    SetupSaveCommand,
)
from scopecat.kernel.content_identity import sha256_json_hash
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
    parameter_revision_hash,
)
from scopecat.records.setup import (
    ActiveSetupView,
    SetupDefinition,
    SetupDefinitionRevision,
    SetupInstrumentBinding,
    SetupRevision,
    SetupRevisionRef,
)

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.instruments.actors import (
    InstrumentActorConflict,
    InstrumentActorRegistry,
    InstrumentActorShutdown,
)
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
        actors: InstrumentActorRegistry,
        devices: DeviceService,
        templates: tuple[ConfigurationTemplate, ...] = (),
    ) -> None:
        self._control = control
        self._registry = config_registry
        self._actors = actors
        self._devices = devices

        self._mutation_lock = Lock()
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

    def current(self) -> ActiveSetupView:
        with self._errors(), self._registry.read_unit_of_work() as work:
            current = work.setups.read_current()
            if current is None:
                raise BackendNotFound("no executable setup is selected")
            return current

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
        definition = SetupDefinitionRevision(
            id=command.revision_id,
            definition=command.setup,
            actor=command.actor,
            note=command.note,
        )
        with (
            self._errors(),
            self._control.write_transaction() as connection,
            self._registry.borrowed_unit_of_work(connection) as work,
        ):
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

    def activate(self, command: SetupActivateCommand) -> ActiveSetupView:
        intent_hash = sha256_json_hash(
            {
                "codec": "scopecat.setup-activation.v1",
                "command": command.model_dump(mode="json", exclude={"operation_id"}),
            }
        )
        with self._mutation_lock, self._errors():
            with self._registry.read_unit_of_work() as work:
                replay = work.setups.read_activation_operation(command.operation_id)
                if replay is not None:
                    if replay.intent_hash != intent_hash:
                        raise BackendConflict(
                            "setup operation id already has a different intent"
                        )
                    return replay.result
                current = work.setups.read_current()
                generation = 0 if current is None else current.activation.generation
                if generation != command.expected_generation:
                    raise BackendConflict("active setup changed")
                target = work.setups.read_revision(command.revision.revision_id)
                if target.ref != command.revision:
                    raise BackendConflict(
                        "setup revision content does not match its reference"
                    )
                if current is None:
                    if command.changes:
                        raise BackendConflict(
                            "initial setup has no inventory to change"
                        )
                    affected_keys = ()
                else:
                    plan = config_registry_service.plan_instrument_inventory_migration(
                        current=current.revision.setup,
                        target=target.setup,
                        declared=_inventory_migration_deltas(command),
                    )
                    affected_keys = plan.affected_exclusivity_keys
            retirement_context = (
                self._actors.begin_retirement(affected_keys)
                if affected_keys
                else nullcontext(None)
            )
            with retirement_context as retirement:
                self._require_drained(affected_keys)
                if retirement is not None:
                    try:
                        retirement.retire_idle()
                    except InstrumentActorConflict, InstrumentActorShutdown:
                        raise
                    except Exception as error:
                        raise BackendConflict(
                            "instrument connection could not be retired safely"
                        ) from error
                with self._control.write_transaction() as connection:
                    blockers = (
                        self._control.inventory_migration_blockers_in_transaction(
                            connection,
                            tuple(ResourceKey.instrument(key) for key in affected_keys),
                        )
                    )
                    _require_no_inventory_migration_blockers(blockers)
                    with self._registry.borrowed_unit_of_work(connection) as work:
                        result = work.setups.activate(
                            revision=command.revision,
                            expected_generation=command.expected_generation,
                            operation_id=command.operation_id,
                            intent_hash=intent_hash,
                            actor=command.actor,
                            note=command.note,
                        )
                    self._control.append_event_in_transaction(
                        connection,
                        DurableEventInput(
                            kind="setup_activated",
                            payload={
                                "revision_id": result.revision.id,
                                "generation": result.activation.generation,
                            },
                            occurred_at=result.activation.recorded_at,
                        ),
                    )

                    # The writer lock and setup generation CAS still fence old readers.
                    if retirement is not None:
                        retirement.release_gate()
                return result

    def _require_drained(self, keys: tuple[str, ...]) -> None:
        with self._control.read_transaction() as connection:
            blockers = self._control.inventory_migration_blockers_in_transaction(
                connection, tuple(ResourceKey.instrument(key) for key in keys)
            )
        _require_no_inventory_migration_blockers(blockers)

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
            InstrumentActorConflict,
            InstrumentActorShutdown,
        ) as error:
            raise BackendConflict(str(error)) from error


def _inventory_migration_deltas(
    command: SetupActivateCommand,
) -> tuple[config_registry_service.InstrumentInventoryMigrationDelta, ...]:
    changes: list[config_registry_service.InstrumentInventoryMigrationDelta] = []
    for change in command.changes:
        if isinstance(change, InstrumentInventoryRemoval):
            changes.append(
                config_registry_service.InstrumentInventoryMigrationDelta(
                    kind="remove",
                    old_instrument_id=change.instrument_id,
                    old_exclusivity_key=change.exclusivity_key,
                )
            )
        elif isinstance(change, InstrumentInventoryRekey):
            changes.append(
                config_registry_service.InstrumentInventoryMigrationDelta(
                    kind="rekey",
                    old_instrument_id=change.instrument_id,
                    old_exclusivity_key=change.from_exclusivity_key,
                    new_instrument_id=change.instrument_id,
                    new_exclusivity_key=change.to_exclusivity_key,
                )
            )
        else:
            assert isinstance(change, InstrumentInventoryRenameRekey)
            changes.append(
                config_registry_service.InstrumentInventoryMigrationDelta(
                    kind="rename_rekey",
                    old_instrument_id=change.from_instrument_id,
                    old_exclusivity_key=change.from_exclusivity_key,
                    new_instrument_id=change.to_instrument_id,
                    new_exclusivity_key=change.to_exclusivity_key,
                )
            )
    return tuple(changes)


def _require_no_inventory_migration_blockers(
    blockers: tuple[InventoryMigrationBlocker, ...],
) -> None:
    if not blockers:
        return
    details = ", ".join(
        f"{blocker.owner_kind} {blocker.owner_id} ({blocker.state}) on {blocker.key.id}"
        for blocker in blockers
    )
    raise BackendConflict(
        f"instrument inventory migration requires drained resources: {details}"
    )
