"""Resource-owned bytes allow ordinary and practice cleanup without shared GC."""

from hashlib import sha256

from scopecat.records.practice import PracticeResource

from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.data_cleanup import require_retained_resource
from scopecat_server.storage.sqlite.object_store import (
    ImmutableObjectStore,
    StoredObject,
)
from scopecat_server.storage.sqlite.practice import PracticeOwnership


def resource_directory(ordinary: ImmutableObjectStore, kind: str, identity: str):
    return ordinary.root / "resources" / kind / sha256(identity.encode()).hexdigest()


def resource_objects(
    database: SQLiteDatabase,
    ordinary: ImmutableObjectStore,
    kind: PracticeResource,
    identity: str,
) -> ImmutableObjectStore:
    del database
    return ImmutableObjectStore(resource_directory(ordinary, kind, identity))


def put_resource_object(
    database: SQLiteDatabase,
    ordinary: ImmutableObjectStore,
    kind: PracticeResource,
    identity: str,
    content: bytes,
) -> StoredObject:
    # Cleaning takes the same writer before removing a scope's object directory.
    # A late writer cannot recreate it, even if preparation began before cleanup.
    with database.writer_guard() as connection:
        owners = PracticeOwnership(connection)
        require_retained_resource(connection, kind, identity)
        owner = owners.owner(kind, identity)
        if owner is not None:
            owners.require_active(owner)
        return ImmutableObjectStore(resource_directory(ordinary, kind, identity)).put(
            content
        )
