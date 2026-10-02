"""Typed retained-input references; inspect source bytes without activating code."""

from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import cast

from pydantic import BaseModel

from scopecat.config.registry.records import ManualConfigDraftRegistrySource
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.project_sources import verified_source_files
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.plan_ref import ExperimentPlanRef, PlanConfigRef
from scopecat.records.run import ConfigRegistryRunConfigSource
from scopecat.records.sample import SampleBinding
from scopecat.records.scientific_scope import TargetMember
from scopecat.records.setup import SetupRevisionRef
from scopecat.records.target_catalog import TargetRevisionRef

from .models import InputRevisionEvidence

type InputReference = (
    ParameterRevisionRef
    | SetupRevisionRef
    | ExperimentPlanRef
    | AuthorRevisionRef
    | SampleBinding
    | TargetMember
    | TargetRevisionRef
    | ConfigContextRef
)


def input_references(value: object) -> Iterator[InputReference]:
    if isinstance(value, PlanConfigRef):
        yield ConfigContextRef(entry_id=value.entry_id, content_hash=value.content_hash)
        return
    if isinstance(value, ConfigRegistryRunConfigSource):
        yield ConfigContextRef(entry_id=value.entry_id, content_hash=value.content_hash)
        return
    if isinstance(value, ManualConfigDraftRegistrySource):
        yield ConfigContextRef(
            entry_id=value.base_entry_id, content_hash=value.base_config_content_hash
        )
        return
    if isinstance(
        value,
        ParameterRevisionRef
        | SetupRevisionRef
        | ExperimentPlanRef
        | AuthorRevisionRef
        | SampleBinding
        | TargetMember
        | TargetRevisionRef
        | ConfigContextRef,
    ):
        yield value
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from input_references(cast("object", getattr(value, name)))
    elif isinstance(value, Mapping):
        for item in cast("Mapping[object, object]", value).values():
            yield from input_references(item)
    elif isinstance(value, tuple | list):
        for item in cast("Iterable[object]", value):
            yield from input_references(item)


def _index[T, K](items: Iterable[T], key: Callable[[T], K]) -> dict[K, T]:
    result: dict[K, T] = {}
    for item in items:
        identity = key(item)
        if identity in result:
            raise ValueError("exchange contains duplicate input revision identities")
        result[identity] = item
    return result


def validate_input_references(
    inputs: InputRevisionEvidence, documents: Iterable[BaseModel] = ()
) -> None:
    parameters = _index(inputs.parameters, lambda item: item.id)
    setups = _index(inputs.setups, lambda item: item.id)
    definitions = _index(inputs.setup_definitions, lambda item: item.id)
    plans = _index(inputs.plans, lambda item: (item.ref.plan_id, item.ref.revision))
    authors = _index(inputs.authors, lambda item: item.manifest.ref.content_hash)
    samples = _index(inputs.samples, lambda item: (item.sample_id, item.revision))
    targets = _index(
        inputs.targets,
        lambda item: (item.ref.catalog_id, item.ref.target_id, item.ref.revision),
    )
    configurations = _index(inputs.configurations, lambda item: item.entry.id)
    for setup in inputs.setups:
        definition = definitions.get(setup.resolution.definition_id)
        if (
            definition is None
            or definition.definition.content_hash != setup.resolution.definition_hash
        ):
            raise ValueError(
                "setup definition is missing or differs from retained resolution"
            )
    for plan in inputs.plans:
        if (
            sha256_json_hash(plan.model_dump(mode="json", exclude={"ref"}))
            != plan.ref.content_hash
        ):
            raise ValueError("plan evidence content identity differs")
    for author in inputs.authors:
        for _name, _content in verified_source_files(author):
            pass
    for ref in input_references((inputs, *documents)):
        matched = False
        match ref:
            case ParameterRevisionRef():
                item = parameters.get(ref.revision_id)
                matched = item is not None and item.ref == ref
            case SetupRevisionRef():
                setup = setups.get(ref.revision_id)
                matched = setup is not None and setup.ref == ref
            case ExperimentPlanRef():
                plan = plans.get((ref.plan_id, ref.revision))
                matched = plan is not None and plan.ref == ref
            case AuthorRevisionRef():
                matched = ref.content_hash in authors
            case SampleBinding() | TargetMember():
                sample = samples.get((ref.sample_id, ref.revision))
                matched = sample is not None and sample.content_hash == ref.content_hash
            case TargetRevisionRef():
                target = targets.get((ref.catalog_id, ref.target_id, ref.revision))
                matched = target is not None and target.ref == ref
            case ConfigContextRef():
                config = configurations.get(ref.entry_id)
                matched = (
                    config is not None and config.entry.content_hash == ref.content_hash
                )
        if not matched:
            raise ValueError(
                f"input revision is missing or differs: {type(ref).__name__}"
            )
