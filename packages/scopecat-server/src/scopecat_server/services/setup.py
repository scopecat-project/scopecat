"""Maintained executable setup selection and device-safe authority changes."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from threading import Lock

from scopecat.config.inventory import (
    InstrumentInventoryRekey,
    InstrumentInventoryRemoval,
    InstrumentInventoryRenameRekey,
)
from scopecat.config.registry import service as config_registry_service
from scopecat.control.models import (
    DurableEventInput,
    InventoryMigrationBlocker,
    ResourceKey,
)
from scopecat.daemon.wire import SetupActivateCommand, SetupSaveCommand
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.kernel.errors import CheckFailed, Conflict, DataIntegrityError, NotFound
from scopecat.records.config import ConfigProfileSnapshot, SystemSpec
from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot
from scopecat.records.setup import ActiveSetupView, SetupRevision

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.instruments.actors import (
    InstrumentActorConflict,
    InstrumentActorRegistry,
    InstrumentActorShutdown,
)
from scopecat_server.storage.sqlite.calibration_cohorts import (
    SQLiteCalibrationCohortStore,
)
from scopecat_server.storage.sqlite.config_registry import SQLiteConfigRegistryStore
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane


def setup_config(revision: SetupRevision) -> ConfigProfileSnapshot:
    """Adapt executable content to existing instrument-only config consumers."""
    setup = revision.setup
    return ConfigProfileSnapshot(
        id=revision.id,
        system=SystemSpec(
            id=revision.id,
            primary_entity_id=setup.primary_entity_id,
            topology=setup.topology,
            instrument_registry=setup.instrument_registry,
            routing=setup.routing,
            domain_target=setup.domain_target,
            parameter_catalog=ParameterCatalog(id=revision.id),
        ),
        parameter_snapshot=ParameterSnapshot(id=revision.id),
    )


class SetupService:
    def __init__(
        self,
        *,
        control: SQLiteControlPlane,
        config_registry: SQLiteConfigRegistryStore,
        actors: InstrumentActorRegistry,
        calibration_cohorts: SQLiteCalibrationCohortStore,
    ) -> None:
        self._control = control
        self._registry = config_registry
        self._actors = actors
        self._cohorts = calibration_cohorts
        self._mutation_lock = Lock()

    def current(self) -> ActiveSetupView:
        with self._errors(), self._registry.read_unit_of_work() as work:
            current = work.setups.read_current()
            if current is None:
                raise BackendNotFound("no executable setup is selected")
            return current

    def get(self, revision_id: str) -> SetupRevision:
        with self._errors(), self._registry.read_unit_of_work() as work:
            return work.setups.read_revision(revision_id)

    def list(self) -> tuple[SetupRevision, ...]:
        with self._errors(), self._registry.read_unit_of_work() as work:
            return work.setups.list_revisions()

    def save(self, command: SetupSaveCommand) -> SetupRevision:
        revision = SetupRevision(
            id=command.revision_id,
            content_hash=command.setup.content_hash,
            setup=command.setup,
            actor=command.actor,
            note=command.note,
        )
        with (
            self._errors(),
            self._control.write_transaction() as connection,
            self._registry.borrowed_unit_of_work(connection) as work,
        ):
            return work.setups.save_revision(revision)

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
            with self._actors.begin_retirement(affected_keys) as retirement:
                self._require_drained(affected_keys)
                retirement.retire_idle()
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
                    self._cohorts.supersede_setup_in_transaction(
                        connection,
                        config=setup_config(result.revision),
                        at=result.activation.recorded_at,
                    )
                    # The writer lock and setup generation CAS still fence old readers.
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
