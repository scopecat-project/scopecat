"""Resolve typed input references, never matching arbitrary strings to record IDs."""

import sqlite3
from collections import deque
from collections.abc import Iterable
from typing import cast

from pydantic import BaseModel
from scopecat.data_exchange import PayloadReference, PayloadSource
from scopecat.data_exchange.input_references import InputReference, input_references
from scopecat.data_exchange.models import ConfigurationEvidence, InputRevisionEvidence
from scopecat.project_sources import verified_source_files
from scopecat.records.author_revision import AuthorRevisionBundle, AuthorRevisionRef
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.experiment_plan import ExperimentPlanRevision
from scopecat.records.parameter_revision import ParameterRevision, ParameterRevisionRef
from scopecat.records.plan_ref import ExperimentPlanRef
from scopecat.records.sample import SampleBinding, SampleRevision
from scopecat.records.sample_artifact import is_owned_sample_artifact_uri
from scopecat.records.scientific_scope import TargetMember
from scopecat.records.setup import (
    SetupDefinitionRevision,
    SetupRevision,
    SetupRevisionRef,
)
from scopecat.records.target_catalog import TargetRevision, TargetRevisionRef

from scopecat_server.storage.sqlite.config_registry import (
    SQLiteConfigRegistryRepository,
)
from scopecat_server.storage.sqlite.experiment_plan_repository import (
    ExperimentPlanRepository,
)
from scopecat_server.storage.sqlite.parameter_revisions import (
    ParameterRevisionRepository,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository


def _configuration_evidence(
    connection: sqlite3.Connection, ref: ConfigContextRef
) -> ConfigurationEvidence:
    repository = SQLiteConfigRegistryRepository(connection)
    entry = repository.read_entry(ref.entry_id)
    if entry.id != ref.entry_id or entry.content_hash != ref.content_hash:
        raise ValueError("configuration evidence differs from retained reference")
    return ConfigurationEvidence(
        entry=entry, configuration=repository.read_config(entry.config_ref)
    )


def capture_input_revisions(
    connection: sqlite3.Connection,
    store: SQLiteProjectStore,
    models: Iterable[BaseModel],
) -> InputRevisionEvidence:
    """Capture transitive plan/input/source revisions in the caller's snapshot.

    Plan ancestry and its exact author revision are followed, including hidden
    plans. Current parameter heads, current setup resolution and available source
    directories are never consulted. Missing records and mismatched hashes fail.
    Other evidence families (analysis, sample artifacts and interpretation) need
    their own resolvers; this function does not claim that they are closed.
    """
    pending = deque(ref for model in models for ref in input_references(model))
    seen: set[InputReference] = set()
    parameters: dict[str, ParameterRevision] = {}
    setups: dict[str, SetupRevision] = {}
    definitions: dict[str, SetupDefinitionRevision] = {}
    plans: dict[tuple[str, int], ExperimentPlanRevision] = {}
    authors: dict[str, AuthorRevisionBundle] = {}
    samples: dict[tuple[str, int], SampleRevision] = {}
    targets: dict[tuple[str, str, int], TargetRevision] = {}
    configurations: dict[str, ConfigurationEvidence] = {}
    setup_repository = SQLiteSetupRepository(connection)
    plan_repository = ExperimentPlanRepository(store)
    while pending:
        ref = pending.popleft()
        if ref in seen:
            continue
        seen.add(ref)
        captured: BaseModel
        match ref:
            case ConfigContextRef():
                item = _configuration_evidence(connection, ref)
                configurations[item.entry.id] = item
                captured = item
            case SampleBinding() | TargetMember():
                row = cast(
                    "sqlite3.Row | None",
                    connection.execute(
                        "SELECT revision_json FROM sample_revisions "
                        "WHERE sample_id=? AND revision=?",
                        (ref.sample_id, ref.revision),
                    ).fetchone(),
                )
                if row is None:
                    raise KeyError(
                        f"missing sample evidence: {ref.sample_id}@{ref.revision}"
                    )
                item = SampleRevision.model_validate_json(cast("str", row[0]))
                if (item.sample_id, item.revision, item.content_hash) != (
                    ref.sample_id,
                    ref.revision,
                    ref.content_hash,
                ):
                    raise ValueError("sample evidence differs from retained reference")
                samples[(item.sample_id, item.revision)] = item
                captured = item
            case TargetRevisionRef():
                row = cast(
                    "sqlite3.Row | None",
                    connection.execute(
                        "SELECT revision_json FROM measurement_target_revisions "
                        "WHERE target_id=? AND revision=?",
                        (ref.target_id, ref.revision),
                    ).fetchone(),
                )
                if row is None:
                    raise KeyError(
                        f"missing target evidence: {ref.target_id}@{ref.revision}"
                    )
                item = TargetRevision.model_validate_json(cast("str", row[0]))
                if item.ref != ref:
                    raise ValueError("target evidence differs from retained reference")
                targets[(ref.catalog_id, ref.target_id, ref.revision)] = item
                captured = item
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
                pending.extend(input_references(definition))
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
        pending.extend(input_references(captured))
    return InputRevisionEvidence(
        parameters=tuple(parameters[key] for key in sorted(parameters)),
        setups=tuple(setups[key] for key in sorted(setups)),
        setup_definitions=tuple(definitions[key] for key in sorted(definitions)),
        plans=tuple(plans[key] for key in sorted(plans)),
        authors=tuple(authors[key] for key in sorted(authors)),
        samples=tuple(samples[key] for key in sorted(samples)),
        targets=tuple(targets[key] for key in sorted(targets)),
        configurations=tuple(configurations[key] for key in sorted(configurations)),
    )


def capture_sample_payloads(
    store: SQLiteProjectStore, revisions: Iterable[SampleRevision]
) -> tuple[PayloadSource, ...]:
    """Retain owned sample bytes; external and unavailable URIs stay inert.

    References come from revisions captured in the enclosing read transaction.
    The exchange writer verifies hashes while streaming these immutable objects.
    No URI is downloaded or treated as a local filesystem path.
    """
    payloads: dict[tuple[str, str], PayloadSource] = {}
    for revision in revisions:
        for artifact in revision.content.artifacts:
            if not is_owned_sample_artifact_uri(artifact.uri):
                continue
            path = store.objects.path_for(artifact.uri)
            ref = f"revisions/{revision.revision}/artifacts/{artifact.id}"
            payloads[(revision.sample_id, ref)] = PayloadSource(
                reference=PayloadReference(
                    owner_kind="sample",
                    owner_id=revision.sample_id,
                    ref=ref,
                    digest=artifact.uri,
                    size=path.stat().st_size,
                ),
                path=path,
            )
    return tuple(payloads[key] for key in sorted(payloads))
