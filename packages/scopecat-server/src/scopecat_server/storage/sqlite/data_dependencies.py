"""Conservative retained-reference inspection at the destructive data boundary."""

import json
import sqlite3
from collections.abc import Iterator
from typing import cast

from pydantic import JsonValue
from scopecat.records.data_cleanup import DataCleanupBlocker, DataCleanupSelection

from scopecat_server.storage.sqlite.data_reference_schema import DATA_REFERENCE_COLUMNS
from scopecat_server.storage.sqlite.object_store import ImmutableObjectStore
from scopecat_server.storage.sqlite.resource_objects import resource_directory


def selection_resources(selection: DataCleanupSelection) -> Iterator[tuple[str, str]]:
    for kind, values in (
        ("run", selection.runs),
        ("analysis", selection.analyses),
        ("procedure", selection.procedures),
        ("setup", selection.setups),
        ("setup_definition", selection.setup_definitions),
        ("parameters", selection.parameters),
    ):
        for identity in values:
            yield kind, identity


def _strings(value: JsonValue) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def retained_dependencies(
    connection: sqlite3.Connection,
    objects: ImmutableObjectStore,
    selection: DataCleanupSelection,
) -> tuple[DataCleanupBlocker, ...]:
    selected = set(selection_resources(selection))
    identities = {identity for _, identity in selected}
    blockers: dict[str, DataCleanupBlocker] = {}

    def inspect(owner: str, content: str | bytes) -> None:
        references = identities.intersection(
            _strings(cast("JsonValue", json.loads(content)))
        )
        if references:
            blockers[owner] = DataCleanupBlocker(
                owner=owner,
                reason="Retained scientific content refers to: "
                + ", ".join(sorted(references)),
            )

    # Records in SQLite carry task decisions, parameter provenance and exact
    # setup bindings. Inspect structured values, never substring matches in JSON.
    for table, identity_column, content_column, kind in DATA_REFERENCE_COLUMNS:
        rows = cast(
            "Iterator[sqlite3.Row]",
            connection.execute(
                f"SELECT {identity_column}, {content_column} FROM {table}"  # noqa: S608 - internal schema constants
            ),
        )
        for row in rows:
            identity = str(cast("str | int", row[0]))
            if (kind, identity) not in selected and row[1] is not None:
                inspect(f"{kind}:{identity}", cast("str", row[1]))

    # Analysis inputs and retained run/config evidence are immutable JSON objects,
    # not fully represented by list summaries in SQLite.
    for row in cast(
        "Iterator[sqlite3.Row]",
        connection.execute(
            "SELECT run_id, ref, digest FROM run_repository_refs "
            "WHERE ref LIKE '%.json'"
        ),
    ):
        identity = cast("str", row[0])
        if ("run", identity) not in selected:
            store = ImmutableObjectStore(resource_directory(objects, "run", identity))
            inspect(f"run:{identity}", store.read(cast("str", row[2])))
    for row in cast(
        "Iterator[sqlite3.Row]",
        connection.execute(
            "SELECT a.record_id, r.digest FROM project_analysis_repository_refs r "
            "JOIN analysis_publications a ON a.sequence=r.publication_sequence "
            "WHERE r.ref LIKE 'records/analysis/%.json'"
        ),
    ):
        identity = cast("str", row[0])
        if ("analysis", identity) not in selected:
            store = ImmutableObjectStore(
                resource_directory(objects, "analysis", identity)
            )
            inspect(f"analysis:{identity}", store.read(cast("str", row[1])))
    for row in cast(
        "Iterator[sqlite3.Row]",
        connection.execute(
            "SELECT plan_id, revision, digest FROM experiment_plan_revisions"
        ),
    ):
        inspect(f"plan:{row[0]}@{row[1]}", objects.read(cast("str", row[2])))
    return tuple(blockers[key] for key in sorted(blockers))
