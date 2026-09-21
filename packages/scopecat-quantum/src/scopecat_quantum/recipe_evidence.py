"""Actual parameter resolution evidence, separate from cached pulse bodies."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import override

from scopecat.authoring.parameter_queries import ParameterQueryResult, QueryMapping
from scopecat.records.parameter import ParameterAtomValue


@dataclass(frozen=True, slots=True)
class ResolvedRecipeInputs(Mapping[str, object]):
    snapshot_id: str
    inputs: QueryMapping[ParameterAtomValue]
    sources: QueryMapping[tuple[ParameterQueryResult, ...]]

    @override
    def __getitem__(self, key: str) -> object:
        return self.inputs[key]

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self.inputs)

    @override
    def __len__(self) -> int:
        return len(self.inputs)


@dataclass(frozen=True, slots=True)
class RecipeInputEvidence:
    """One resolved implementation's inputs; not implementation code provenance."""

    recipe_id: str
    implementation_id: str
    implementation_fingerprint: str
    scope: str | None
    resolution: ResolvedRecipeInputs
