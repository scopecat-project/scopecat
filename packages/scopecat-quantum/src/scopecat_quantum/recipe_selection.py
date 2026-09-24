"""Transient author recipe selection, distinct from device payloads."""

from collections.abc import Callable
from typing import Protocol

from scopecat_quantum._ids import GateId
from scopecat_quantum.acquisitions import AcquisitionKind
from scopecat_quantum.recipe_queries import RecipeParameterInputs


class SelectableRecipes(Protocol):
    """Low-level selection boundary, independent of authoring implementation imports."""

    def selection_identity(self) -> object: ...

    def declarative_inputs(
        self,
        *,
        gate_id: GateId | None = None,
        measurement_kind: AcquisitionKind | None = None,
    ) -> tuple[RecipeParameterInputs, ...]: ...

    @property
    def materialize_quantum(self) -> Callable[..., object]: ...


class RecipeSelection:
    """One immutable-by-convention profile with a stable declaration identity."""

    __slots__ = ("_identity", "_profile")

    def __init__(self, profile: SelectableRecipes) -> None:
        self._profile = profile
        self._identity = profile.selection_identity()

    @property
    def profile(self) -> SelectableRecipes:
        return self._profile

    def __scopecat_fingerprint__(self) -> object:
        return self._identity
