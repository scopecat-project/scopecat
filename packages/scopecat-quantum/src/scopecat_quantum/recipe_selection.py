"""Transient author recipe selection, distinct from device payloads."""

from collections.abc import Callable
from typing import Protocol


class SelectableRecipes(Protocol):
    """Low-level selection boundary, independent of authoring implementation imports."""

    def selection_identity(self) -> object: ...

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
