"""Input exchange follows retained identities without a device/source runtime."""

from base64 import b64encode
from pathlib import Path

import pytest
from scopecat.kernel.content_identity import sha256_content_hash
from scopecat.records.author_revision import (
    AuthorRevisionBundle,
    AuthorRevisionManifest,
)
from scopecat.records.config import RoutingGraph, Topology
from scopecat.records.experiment_plan import (
    ExperimentPlanDefinition,
    ExperimentPlanSave,
)
from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot
from scopecat.records.parameter_revision import (
    ParameterRevision,
    parameter_revision_hash,
)
from scopecat.records.run_request import RunRequest
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    UnboundSubject,
)
from scopecat.records.scientific_selection import (
    ParameterConfiguration,
    ScientificSelection,
)
from scopecat.records.setup import SetupDefinition, SetupDefinitionRevision

from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.evidence_inputs import capture_input_revisions
from scopecat_server.storage.sqlite.experiment_plan_repository import (
    ExperimentPlanRepository,
)
from scopecat_server.storage.sqlite.parameter_revisions import (
    ParameterRevisionRepository,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository


def test_hidden_plan_ancestry_keeps_exact_parameter_and_setup_revisions(tmp_path: Path):
    store = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "data.sqlite"), tmp_path / "objects"
    )
    store.bootstrap()
    catalog = ParameterCatalog(id="test")
    values = ParameterSnapshot(id="test")
    parameters = ParameterRevision(
        id="parameters-one",
        catalog=catalog,
        parameters=values,
        content_hash=parameter_revision_hash(catalog, values),
        actor="test",
    )
    definition = SetupDefinitionRevision(
        id="logical-bench",
        actor="test",
        definition=SetupDefinition(
            topology=Topology(),
            instruments=(),
            routing=RoutingGraph(),
            domain_target=None,
        ),
    )
    with store.sqlite.write_transaction() as connection:
        ParameterRevisionRepository(connection).save(parameters)
        setups = SQLiteSetupRepository(connection)
        setups.save_definition(definition)
        setup = setups.resolve(definition.id)
    plan_definition = ExperimentPlanDefinition(
        workspace_id="unavailable-source",
        experiment="signal",
        version="1",
        definition_hash="sha256:" + "1" * 64,
        selection=ScientificSelection(
            configuration=ParameterConfiguration(
                ref=parameters.ref,
                setup=setup.ref,
            )
        ),
        scientific_binding=ResolvedScientificBinding(
            subject=UnboundSubject(),
            config_content_hash="sha256:" + "2" * 64,
            setup_content_hash=setup.setup.execution_content_hash,
        ),
    )
    plans = ExperimentPlanRepository(store)
    first = plans.save(
        ExperimentPlanSave(
            name="original",
            saved_by="test",
            definition=plan_definition,
        )
    )
    second = plans.save(
        ExperimentPlanSave(
            name="revised",
            saved_by="test",
            definition=plan_definition,
            previous=first.ref,
        )
    )
    plans.hide(second.ref)
    with store.sqlite.read_transaction() as connection:
        evidence = capture_input_revisions(
            connection,
            store,
            (
                RunRequest(
                    plan_ref=second.ref, metadata={"not_a_ref": "missing-parameters"}
                ),
            ),
        )
    assert evidence.parameters == (parameters,)
    assert evidence.setups == (setup,)
    assert evidence.setup_definitions == (definition,)
    assert evidence.plans == (first, second)
    assert not evidence.authors
    with (
        store.sqlite.read_transaction() as connection,
        pytest.raises(ValueError, match="parameter evidence hash"),
    ):
        capture_input_revisions(
            connection,
            store,
            (parameters.ref.model_copy(update={"content_hash": "sha256:" + "f" * 64}),),
        )

    # Missing references are errors, never silently replaced by current values.
    with store.sqlite.write_transaction() as connection:
        connection.execute(
            "DELETE FROM parameter_revisions WHERE revision_id=?", (parameters.id,)
        )
    with store.sqlite.read_transaction() as connection, pytest.raises(KeyError):
        capture_input_revisions(connection, store, (RunRequest(plan_ref=second.ref),))
    store.close()


def test_retained_author_evidence_is_verified_without_extracting_source(tmp_path: Path):
    store = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "data.sqlite"), tmp_path / "objects"
    )
    store.bootstrap()
    source = b'raise AssertionError("source must not execute during data capture")\n'
    manifest = AuthorRevisionManifest(
        files={"experiment.py": sha256_content_hash(source)},
        source_roots=(),
        refresh_roots=(),
        python="3.14",
        packages={},
        import_packages={},
        maintenance_hash="sha256:" + "0" * 64,
    )
    bundle = AuthorRevisionBundle(
        manifest=manifest,
        files={"experiment.py": b64encode(source).decode()},
    )
    digest = store.objects.put(bundle.model_dump_json().encode()).digest
    with store.sqlite.write_transaction() as connection:
        connection.execute(
            "INSERT INTO author_revisions VALUES (?,?)",
            (
                manifest.ref.content_hash,
                digest,
            ),
        )
    with store.sqlite.read_transaction() as connection:
        evidence = capture_input_revisions(connection, store, (manifest.ref,))
    assert evidence.authors == (bundle,)
    assert not list(tmp_path.rglob("experiment.py"))

    damaged = bundle.model_copy(
        update={"files": {"experiment.py": b64encode(b"wrong").decode()}}
    )
    digest = store.objects.put(damaged.model_dump_json().encode()).digest
    with store.sqlite.write_transaction() as connection:
        connection.execute("UPDATE author_revisions SET bundle_digest=?", (digest,))
    with (
        store.sqlite.read_transaction() as connection,
        pytest.raises(ValueError, match="source checksum mismatch"),
    ):
        capture_input_revisions(connection, store, (manifest.ref,))
    store.close()
