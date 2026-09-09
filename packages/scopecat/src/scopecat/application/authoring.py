"""A maintained discovery adapter for ordinary experiment authors.

Discovery is explicit project composition, not an HTTP import facility. A loaded
collection is immutable; refreshing code and retaining historical implementations
are separate from this initial authoring path.
"""

from __future__ import annotations

import inspect
import pkgutil
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from importlib import import_module
from types import MappingProxyType
from typing import cast

from pydantic import BaseModel, ConfigDict, JsonValue

from scopecat.api.lab import LabClient, PreparedLabExperiment
from scopecat.api.procedures import LabProcedureContext
from scopecat.api.run import RunHandle
from scopecat.application.controls import (
    ControlEdit,
    control_catalog,
    control_values,
    edit_controls,
)
from scopecat.application.launch import (
    LaunchCatalog,
    LaunchCatalogEntry,
    LaunchConfigSource,
    LaunchInputSchema,
    LaunchPreview,
    LaunchProvider,
    LaunchRequest,
    LaunchResult,
    LaunchSubmission,
)
from scopecat.application.launch_config import (
    launch_config_generation,
    launch_preflight_configuration,
    launch_preflight_meaning,
    launch_sample_selection,
    resolve_launch_config,
)
from scopecat.authoring.experiments import Experiment
from scopecat.automation.definition import RegisteredProcedure
from scopecat.automation.models import ProcedureDefinitionRef, procedure_intent_hash
from scopecat.daemon.views import ConfigContextResolution
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.kernel.frozen import thaw_json_value
from scopecat.kernel.python_source import python_source_identity
from scopecat.planning.preflight import (
    ExactQuantity,
    PreflightSummary,
    summarize_preflight,
)
from scopecat.program.controls import ControlScalar, ControlSet
from scopecat.program.definitions import ExperimentInvocation
from scopecat.program.scans import AxisSpec
from scopecat.program.values import MetadataValue
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.content import Sha256ContentHash
from scopecat.records.sample import SampleSelector


class AuthorLaunchIntent(BaseModel):
    """Same frozen admission inputs for every discovered single-run experiment."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    config: ConfigProfileSnapshot
    config_source: LaunchConfigSource
    edits: dict[str, ControlEdit]
    actor: str
    request_hash: Sha256ContentHash


@dataclass(frozen=True, slots=True)
class AuthorExperiment:
    """One discovered declaration and its default invocation, without a registry DSL."""

    invocation: ExperimentInvocation
    controls: ControlSet
    source: Mapping[str, str]
    title: str
    description: str
    fingerprint: Sha256ContentHash = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", MappingProxyType(dict(self.source)))
        object.__setattr__(
            self,
            "fingerprint",
            sha256_json_hash(
                {
                    "codec": "scopecat.author-experiment.v1",
                    "id": self.invocation.definition.id,
                    "declaration": dict(self.source),
                    "controls": [
                        item.model_dump(mode="json")
                        for item in control_catalog(self.controls)
                    ],
                    "wrapper": python_source_identity(
                        _AuthorProcedure.run, label="author wrapper"
                    ),
                    "intent": AuthorLaunchIntent.model_json_schema(),
                }
            ),
        )

    @property
    def entry(self) -> LaunchCatalogEntry:
        return LaunchCatalogEntry(
            id=self.invocation.definition.id,
            version=self.fingerprint,
            title=self.title,
            description=self.description,
            actions=("preview", "submit"),
            kind="diagnostic",
            configuration_effect="none",
            request=LaunchInputSchema(properties={}),
            controls=control_catalog(self.controls),
        )

    def edit(
        self,
        *,
        config: ConfigProfileSnapshot,
        edits: dict[str, ControlEdit] | None = None,
    ) -> ExperimentInvocation:
        return edit_controls(
            self.controls, self.invocation, config=config, edits=edits or {}
        )

    @property
    def provenance(self) -> dict[str, MetadataValue]:
        return {
            "author_declaration": dict(self.source),
            "author_fingerprint": self.fingerprint,
            "author_source_scope": (
                "declaration-and-controls; not transitive helper identity"
            ),
        }

    def prepare(
        self,
        lab: LabClient,
        *,
        config: str
        | ConfigProfileSnapshot
        | ConfigContextRef
        | ConfigContextResolution
        | None = None,
        edits: Mapping[str, ControlScalar | AxisSpec] | None = None,
    ) -> PreparedLabExperiment:
        prepared = lab.prepare(self.invocation, config=config)
        return replace(
            prepared,
            invocation=self.controls.apply(
                self.invocation, config=prepared.config, edits=edits
            ),
        )

    def run(
        self,
        lab: LabClient,
        *,
        config: str
        | ConfigProfileSnapshot
        | ConfigContextRef
        | ConfigContextResolution
        | None = None,
        edits: Mapping[str, ControlScalar | AxisSpec] | None = None,
        sample: str | SampleSelector | None = None,
        operator: str | None = None,
    ) -> RunHandle:
        return self.prepare(lab, config=config, edits=edits).run(
            name=self.title, metadata=self.provenance, sample=sample, operator=operator
        )


@dataclass(frozen=True, slots=True)
class _AuthorProcedure:
    """Implement the existing registry protocol; authors never write a procedure."""

    experiment: AuthorExperiment

    @property
    def id(self) -> str:
        return f"scopecat.author:{self.experiment.entry.id}"

    @property
    def version(self) -> str:
        return "1"

    @property
    def fingerprint(self) -> Sha256ContentHash:
        return self.experiment.fingerprint

    @property
    def intent_type(self) -> type[AuthorLaunchIntent]:
        return AuthorLaunchIntent

    @property
    def ref(self) -> ProcedureDefinitionRef:
        return ProcedureDefinitionRef(
            id=self.id, version=self.version, fingerprint=self.fingerprint
        )

    def validate_intent(self, value: object) -> AuthorLaunchIntent:
        return AuthorLaunchIntent.model_validate(thaw_json_value(value))

    def encode_intent(self, value: object) -> dict[str, JsonValue]:
        return cast(
            "dict[str, JsonValue]", self.validate_intent(value).model_dump(mode="json")
        )

    def intent_hash(self, value: object) -> Sha256ContentHash:
        return procedure_intent_hash(self.ref, self.encode_intent(value))

    def run(self, context: object, intent: object) -> None:
        context = cast("LabProcedureContext", context)
        selected = self.validate_intent(intent)
        context.run(
            "experiment",
            self.experiment.edit(config=selected.config, edits=selected.edits),
            config=selected.config,
            config_source=selected.config_source,
            operator=selected.actor,
            metadata=self.experiment.provenance,
        )


@dataclass(frozen=True, slots=True)
class AuthorExperiments:
    """Immutable, explicitly discovered experiments composing existing launch APIs."""

    experiments: tuple[AuthorExperiment, ...]

    def __post_init__(self) -> None:
        ids = [experiment.entry.id for experiment in self.experiments]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate author experiment IDs")

    @classmethod
    def discover(cls, *sources: str) -> AuthorExperiments:
        """Discover locally defined experiments from maintained modules/packages."""
        discovered: list[AuthorExperiment] = []
        names: set[str] = set()
        for source in sources:
            root = import_module(source)
            names.add(root.__name__)
            if hasattr(root, "__path__"):
                names.update(
                    item.name
                    for item in pkgutil.walk_packages(
                        root.__path__, f"{root.__name__}."
                    )
                )
        for name in sorted(names):
            module = import_module(name)
            for value in cast("dict[str, object]", vars(module)).values():
                if (
                    not isinstance(value, Experiment)
                    or value.__wrapped__.__module__ != name
                ):
                    continue
                experiment = value
                try:
                    invocation = experiment.bind()
                    controls = invocation.definition.controls
                    if not isinstance(controls, ControlSet):
                        raise TypeError(
                            "declare a ControlSet for the author launch form"
                        )
                    missing = [
                        item.id
                        for item in invocation.definition.inputs
                        if item.required
                        and not item.has_default
                        and item.id not in invocation.input_overrides
                    ]
                    if missing:
                        raise ValueError(
                            f"missing input defaults: {', '.join(missing)}"
                        )
                    source_identity = python_source_identity(
                        experiment.__wrapped__, label=experiment.id
                    )
                    discovered.append(
                        AuthorExperiment(
                            invocation=invocation,
                            controls=controls,
                            source={
                                "module": source_identity["module"],
                                "qualname": source_identity["qualname"],
                                "source": source_identity["source"],
                            },
                            title=str(
                                experiment.metadata.get(
                                    "title", experiment.__name__.replace("_", " ")
                                )
                            ),
                            description=inspect.getdoc(experiment.__wrapped__)
                            or experiment.id,
                        )
                    )
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        f"{name}:{experiment.__name__}: {error}"
                    ) from error
        return cls(tuple(discovered))

    @property
    def procedures(self) -> tuple[RegisteredProcedure, ...]:
        return tuple(_AuthorProcedure(experiment) for experiment in self.experiments)

    def get(self, id: str) -> AuthorExperiment:
        for experiment in self.experiments:
            if experiment.entry.id == id:
                return experiment
        raise KeyError(id)

    def compose(self, maintained: LaunchProvider | None) -> AuthorLaunchProvider:
        """Retain this collection when an application is copied with replace()."""
        return AuthorLaunchProvider(authors=self, maintained=maintained)

    def launch(
        self, lab: LabClient, request: LaunchRequest, maintained: LaunchProvider | None
    ) -> LaunchResult:
        existing = (
            maintained(lab, LaunchRequest(action="list"))
            if maintained is not None
            else LaunchCatalog()
        )
        if not isinstance(existing, LaunchCatalog):
            raise TypeError("maintained list callback must return LaunchCatalog")
        entries = (*existing.entries, *(item.entry for item in self.experiments))
        if len({entry.id for entry in entries}) != len(entries):
            raise ValueError("author and maintained launch IDs overlap")
        if request.action == "list":
            return LaunchCatalog(entries=entries)
        selected = next(
            (item for item in self.experiments if item.entry.id == request.experiment),
            None,
        )
        if selected is None:
            if maintained is None:
                raise ValueError(f"unknown author experiment {request.experiment!r}")
            return maintained(lab, request)
        if request.version != selected.entry.version:
            raise ValueError(
                "author declaration changed; reload the catalog and preview again"
            )
        if request.inputs:
            raise ValueError(
                "author experiments accept declared control edits, not extra inputs"
            )
        config, source = resolve_launch_config(lab, request)
        invocation = selected.edit(config=config, edits=request.control_edits)
        if request.action == "preview":
            preview = lab.preview_invocation(
                invocation, config=config, config_source=source
            )
            return LaunchPreview(
                experiment_id=selected.entry.id,
                request_hash=request.request_hash,
                config_source=source,
                point_count=preview.initial_point_count,
                controls=control_values(selected.controls, invocation, config=config),
                summary=selected.description,
                preflight=PreflightSummary(
                    stages=(
                        summarize_preflight(
                            preview,
                            stage_id="experiment",
                            label=selected.title,
                            configuration=launch_preflight_configuration(source),
                            executions=ExactQuantity(
                                value=1,
                                unit="runs",
                                basis="One authored experiment",
                            ),
                            config_content_hash=source.content_hash,
                            configuration_meaning=launch_preflight_meaning(source),
                        ),
                    ),
                    scope_basis="One authored run; all selected points.",
                ),
            )
        admitted = lab.procedures.submit(
            _AuthorProcedure(selected),
            AuthorLaunchIntent(
                config=config,
                config_source=source,
                edits=request.control_edits,
                actor=request.actor,
                request_hash=request.request_hash,
            ),
            request_key=request.request_key,
            sample=launch_sample_selection(request, source),
            expected_config_generation=launch_config_generation(source),
        )
        return LaunchSubmission(procedure_id=admitted.id)


@dataclass(frozen=True, slots=True)
class AuthorLaunchProvider:
    """A composed provider retains its already discovered author collection."""

    authors: AuthorExperiments
    maintained: LaunchProvider | None

    def __call__(self, lab: LabClient, request: LaunchRequest) -> LaunchResult:
        return self.authors.launch(lab, request, self.maintained)
