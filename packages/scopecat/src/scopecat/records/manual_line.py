"""Attribution and freshness evidence for explicitly manual physical lines.

These records do not identify a scientific target or grant device permissions.
They retain a human wiring confirmation for one frozen child-run intent.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from scopecat.records.content import Sha256ContentHash

type _Text = Annotated[str, Field(min_length=1, pattern=r"\S")]


class ManualLineIntent(BaseModel):
    """The exact child step and frozen acquisition being confirmed."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    line_id: _Text
    step_key: _Text
    intent_hash: Sha256ContentHash


class ManualLineConfirmationRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    procedure_run_id: _Text
    step_key: _Text
    request_hash: Sha256ContentHash


class ManualLineConfirmation(ManualLineConfirmationRef):
    """Server-resolved original confirmation retained with admission."""

    line_id: _Text
    runtime_id: _Text
    actor: _Text


class ManualLineReconfirmation(BaseModel):
    """One idempotent human confirmation of the original frozen run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_key: _Text
    actor: _Text
    original: ManualLineConfirmation
