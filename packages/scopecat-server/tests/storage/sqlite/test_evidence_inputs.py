"""Input exchange follows retained identities without a device/source runtime."""

from base64 import b64encode
from pathlib import Path

import pytest
from scopecat.config.registry.records import (
    ConfigRegistryEntry,
    DirectConfigRegistrySource,
    ManualConfigDraftRegistrySource,
)
from scopecat.daemon.wire import SampleCreateCommand, SampleReviseCommand
from scopecat.data_exchange.input_references import validate_input_references
from scopecat.kernel.content_identity import sha256_content_hash
from scopecat.records.author_revision import (
    AuthorRevisionBundle,
    AuthorRevisionManifest,
)
from scopecat.records.config import RoutingGraph, Topology, config_content_hash
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.experiment_plan import (
    ExperimentPlanDefinition,
    ExperimentPlanSave,
)
from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot
from scopecat.records.parameter_revision import (
    ParameterRevision,
    parameter_revision_hash,
)
from scopecat.records.plan_ref import PlanConfigRef
from scopecat.records.run import ConfigRegistryRunConfigSource
from scopecat.records.run_request import RunRequest
from scopecat.records.sample import (
    SampleArtifactRef,
    SampleBinding,
    SampleRevisionDraft,
)
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    UnboundSubject,
)
from scopecat.records.scientific_scope import MeasurementTarget, TargetMember
from scopecat.records.scientific_selection import (
    ParameterConfiguration,
    ScientificSelection,
)
from scopecat.records.setup import SetupDefinition, SetupDefinitionRevision
from scopecat.records.target_catalog import (
    TargetCreateCommand,
    TargetReviseCommand,
    TargetRevisionDraft,
)
from scopecat_testkit.workflow_fixtures import load_config

from scopecat_server.storage.sqlite.config_registry import (
    SQLiteConfigRegistryRepository,
)
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane
from scopecat_server.storage.sqlite.evidence_inputs import (
    capture_input_revisions,
    capture_sample_payloads,
)
from scopecat_server.storage.sqlite.experiment_plan_repository import (
    ExperimentPlanRepository,
)
from scopecat_server.storage.sqlite.parameter_revisions import (
    ParameterRevisionRepository,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.samples import SQLiteSampleStore
from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository
from scopecat_server.storage.sqlite.target_catalog import TargetCatalogStore


def test_registry_capture_follows_exact_base_without_activation(tmp_path: Path):
    store = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "data.sqlite"), tmp_path / "objects"
    )
    store.bootstrap()
    config = load_config()
    digest = config_content_hash(config)
    with store.sqlite.write_transaction() as connection:
        registry = SQLiteConfigRegistryRepository(connection)
        original = ConfigRegistryEntry(
            id="original",
            config_ref=registry.config_ref("original"),
            content_hash=digest,
            actor="test",
            source=DirectConfigRegistrySource(),
        )
        edited = ConfigRegistryEntry(
            id="edited",
            config_ref=registry.config_ref("edited"),
            content_hash=digest,
            actor="test",
            source=ManualConfigDraftRegistrySource(
                base_entry_id=original.id,
                base_config_content_hash=digest,
                base_registry_generation=1,
            ),
        )
        for entry in (original, edited):
            registry.commit_revision(entry=entry, config=config)
    source = ConfigRegistryRunConfigSource(
        selector="retained",
        entry_id=edited.id,
        config_ref=edited.config_ref,
        content_hash=digest,
    )
    with store.sqlite.read_transaction() as connection:
        evidence = capture_input_revisions(connection, store, (source,))
        assert [item.entry.id for item in evidence.configurations] == [
            "edited",
            "original",
        ]
        assert all(item.configuration == config for item in evidence.configurations)
        assert (
            capture_input_revisions(
                connection,
                store,
                (
                    PlanConfigRef(
                        entry_id=edited.id,
                        content_hash=digest,
                    ),
                ),
            )
            == evidence
        )
        with pytest.raises(ValueError, match="configuration evidence differs"):
            capture_input_revisions(
                connection,
                store,
                (
                    ConfigContextRef(
                        entry_id=edited.id,
                        content_hash="sha256:" + "f" * 64,
                    ),
                ),
            )
    store.close()


def test_sample_and_target_capture_retains_exact_revisions(tmp_path: Path):
    store = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "data.sqlite"), tmp_path / "objects"
    )
    store.bootstrap()
    samples = SQLiteSampleStore(store.sqlite, control=SQLiteControlPlane(store.sqlite))
    content = b"original sample report"
    attachment = store.objects.put(content)
    first = samples.create_sample(
        SampleCreateCommand(
            operation_id="create",
            sample_id="sample",
            kind="chip",
            actor="test",
            content=SampleRevisionDraft(
                display_name="Original",
                artifacts=(
                    SampleArtifactRef(
                        id="report",
                        title="Report",
                        uri=attachment.digest,
                        media_type="text/plain",
                    ),
                    SampleArtifactRef(
                        id="external",
                        title="External",
                        uri="https://example.invalid/report",
                    ),
                    SampleArtifactRef(
                        id="unavailable",
                        title="Unavailable",
                        uri="/never/read/local/file",
                    ),
                ),
            ),
        )
    ).revision
    member = TargetMember(
        id="subject",
        sample_id=first.sample_id,
        revision=first.revision,
        content_hash=first.content_hash,
    )
    catalog = TargetCatalogStore(store.sqlite, catalog_id="retained-catalog")
    target = catalog.create(
        TargetCreateCommand(
            catalog_id="retained-catalog",
            target_id="target",
            draft=TargetRevisionDraft(
                name="Original target",
                content=MeasurementTarget(members=(member,)),
                actor="test",
            ),
        )
    )
    samples.revise_sample(
        "sample",
        SampleReviseCommand(
            operation_id="revise",
            expected_revision=1,
            actor="test",
            content=SampleRevisionDraft(display_name="Current"),
        ),
    )
    catalog.revise(
        TargetReviseCommand(
            expected=target.ref,
            draft=TargetRevisionDraft(
                name="Current target",
                content=target.content,
                actor="test",
            ),
        )
    )
    binding = SampleBinding(
        role="subject",
        sample_id=first.sample_id,
        revision=first.revision,
        content_hash=first.content_hash,
        kind="chip",
        display_name="Original",
    )
    with store.sqlite.read_transaction() as connection:
        evidence = capture_input_revisions(connection, store, (target.ref, binding))
        assert evidence.samples == (first,)
        assert evidence.targets == (target,)
        validate_input_references(evidence, (target.ref, binding))
        with pytest.raises(ValueError, match="input revision is missing"):
            validate_input_references(
                evidence.model_copy(update={"samples": ()}), (target.ref, binding)
            )
        payloads = capture_sample_payloads(store, evidence.samples)
        assert len(payloads) == 1
        assert payloads[0].reference.owner_id == first.sample_id
        assert payloads[0].reference.digest == attachment.digest
        assert payloads[0].path.read_bytes() == content
        for ref in (
            binding.model_copy(update={"content_hash": "sha256:" + "f" * 64}),
            target.ref.model_copy(update={"catalog_id": "foreign-catalog"}),
        ):
            with pytest.raises(ValueError, match="evidence differs"):
                capture_input_revisions(connection, store, (ref,))
        for ref in (
            binding.model_copy(update={"revision": 99}),
            target.ref.model_copy(update={"revision": 99}),
        ):
            with pytest.raises(KeyError, match=r"missing .* evidence"):
                capture_input_revisions(connection, store, (ref,))
    store.objects.path_for(attachment.digest).unlink()
    with pytest.raises(FileNotFoundError):
        capture_sample_payloads(store, (first,))
    store.close()


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
    validate_input_references(evidence, (second.ref,))
    for family in ("parameters", "setups", "setup_definitions", "plans"):
        with pytest.raises(ValueError, match=r"missing|differs"):
            validate_input_references(
                evidence.model_copy(update={family: ()}), (second.ref,)
            )
    with pytest.raises(ValueError, match="input revision is missing"):
        validate_input_references(evidence.model_copy(update={"plans": (second,)}))
    with pytest.raises(ValueError, match="duplicate input revision"):
        validate_input_references(
            evidence.model_copy(update={"parameters": (parameters, parameters)})
        )
    with pytest.raises(ValueError, match="plan evidence content identity"):
        validate_input_references(
            evidence.model_copy(
                update={"plans": (first.model_copy(update={"name": "changed"}), second)}
            )
        )
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
        import_requirements=(),
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
    validate_input_references(evidence, (manifest.ref,))
    assert not list(tmp_path.rglob("experiment.py"))

    damaged = bundle.model_copy(
        update={"files": {"experiment.py": b64encode(b"wrong").decode()}}
    )
    with pytest.raises(ValueError, match="source checksum mismatch"):
        validate_input_references(
            evidence.model_copy(update={"authors": (damaged,)}), (manifest.ref,)
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
