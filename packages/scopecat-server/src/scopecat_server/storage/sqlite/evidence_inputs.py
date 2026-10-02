"""Resolve typed input references, never matching arbitrary strings to record IDs."""

import sqlite3
from collections import deque
from collections.abc import Iterable, Iterator, Mapping
from typing import cast

from pydantic import BaseModel
from scopecat.project_sources import verified_source_files
from scopecat.records.author_revision import AuthorRevisionBundle, AuthorRevisionRef
from scopecat.records.exchange import InputRevisionEvidence
from scopecat.records.experiment_plan import ExperimentPlanRevision
from scopecat.records.parameter_revision import ParameterRevision, ParameterRevisionRef
from scopecat.records.plan_ref import ExperimentPlanRef
from scopecat.records.setup import (
    SetupDefinitionRevision,
    SetupRevision,
    SetupRevisionRef,
)

from scopecat_server.storage.sqlite.experiment_plan_repository import (
    ExperimentPlanRepository,
)
from scopecat_server.storage.sqlite.parameter_revisions import (
    ParameterRevisionRepository,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository

type InputReference = (
    ParameterRevisionRef | SetupRevisionRef | ExperimentPlanRef | AuthorRevisionRef
)


def _references(value: object) -> Iterator[InputReference]:
    if isinstance(
        value,
        ParameterRevisionRef | SetupRevisionRef | ExperimentPlanRef | AuthorRevisionRef,
    ):
        yield value
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from _references(cast("object", getattr(value, name)))
    elif isinstance(value, Mapping):
        for item in cast("Mapping[object, object]", value).values():
            yield from _references(item)
    elif isinstance(value, tuple | list):
        for item in cast("Iterable[object]", value):
            yield from _references(item)


def capture_input_revisions(
    connection: sqlite3.Connection,
    store: SQLiteProjectStore,
    models: Iterable[BaseModel],
) -> InputRevisionEvidence:
    """Capture transitive plan/input/source revisions in the caller's snapshot.

    Plan ancestry and its exact author revision are followed, including hidden
    plans. Current parameter heads, current setup resolution and available source
    directories are never consulted. Missing records and mismatched hashes fail.
    Other evidence families (analysis, sample and interpretation) are captured by
    their own resolvers; this function does not claim that they are closed.
    """
    pending = deque(ref for model in models for ref in _references(model))
    seen: set[InputReference] = set()
    parameters: dict[str, ParameterRevision] = {}
    setups: dict[str, SetupRevision] = {}
    definitions: dict[str, SetupDefinitionRevision] = {}
    plans: dict[tuple[str, int], ExperimentPlanRevision] = {}
    authors: dict[str, AuthorRevisionBundle] = {}
    setup_repository = SQLiteSetupRepository(connection)
    plan_repository = ExperimentPlanRepository(store)
    while pending:
        ref = pending.popleft()
        if ref in seen:
            continue
        seen.add(ref)
        captured: BaseModel
        match ref:
            case ParameterRevisionRef():
                item = ParameterRevisionRepository(connection).get(ref.revision_id)
                if item.ref != ref:
                    raise ValueError("parameter evidence hash does not match reference")
                parameters[item.id] = item
                captured = item
            case SetupRevisionRef():
                item = setup_repository.read_revision(ref.revision_id)
                if item.ref != ref:
                    raise ValueError("setup evidence hash does not match reference")
                definition = setup_repository.definition(item.resolution.definition_id)
                if (
                    definition.definition.content_hash
                    != item.resolution.definition_hash
                ):
                    raise ValueError("setup definition differs from its resolution")
                definitions[definition.id] = definition
                setups[item.id] = item
                pending.extend(_references(definition))
                captured = item
            case ExperimentPlanRef():
                item = plan_repository.get_in_transaction(connection, ref)
                plans[(ref.plan_id, ref.revision)] = item
                captured = item
            case AuthorRevisionRef():
                row = cast(
                    "sqlite3.Row | None",
                    connection.execute(
                        "SELECT bundle_digest FROM author_revisions "
                        "WHERE content_hash=?",
                        (ref.content_hash,),
                    ).fetchone(),
                )
                if row is None:
                    raise KeyError(f"missing author evidence: {ref.content_hash}")
                bundle = AuthorRevisionBundle.model_validate_json(
                    store.objects.read(cast("str", row[0]))
                )
                if bundle.manifest.ref != ref:
                    raise ValueError("author evidence hash does not match reference")
                for _name, _content in verified_source_files(bundle):
                    pass
                authors[ref.content_hash] = bundle
                captured = bundle
        pending.extend(_references(captured))
    return InputRevisionEvidence(
        parameters=tuple(parameters[key] for key in sorted(parameters)),
        setups=tuple(setups[key] for key in sorted(setups)),
        setup_definitions=tuple(definitions[key] for key in sorted(definitions)),
        plans=tuple(plans[key] for key in sorted(plans)),
        authors=tuple(authors[key] for key in sorted(authors)),
    )
