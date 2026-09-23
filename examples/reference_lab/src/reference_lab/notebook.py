"""Small presentation helpers for executable notebook recipes."""

from __future__ import annotations

from pprint import pprint
from uuid import uuid4

from scopecat.api.lab import LabClient
from scopecat.daemon.views import ParameterResolution

from reference_lab.configuration import initial_parameters


def gallery_inputs(lab: LabClient) -> ParameterResolution:
    """Resolve fixed acceptance inputs without selecting a global default.

    This maintainer-owned gallery is a device test fixture, not an author template.
    Each script run retains its own parameter revision and operator provenance.
    """
    content = initial_parameters()
    saved = lab.parameters.save(
        name=f"gallery-inputs-{uuid4().hex}",
        catalog=content.catalog,
        parameters=content.parameters,
    )
    return lab.parameters.resolve(saved, setup=lab.setup.active().revision.ref)


def show(value: object) -> None:
    """Render a value readably in both notebook cells and terminal runs."""
    pprint(value, sort_dicts=False)
