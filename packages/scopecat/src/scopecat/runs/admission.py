"""Build the immutable accepted run skeleton."""

from __future__ import annotations

from dataclasses import dataclass

from scopecat.kernel.ids import new_run_id
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.run import RunConfigSource, RunSnapshot
from scopecat.records.run_request import RunRequest
from scopecat.records.sample import SampleBinding
from scopecat.records.scientific_binding import ResolvedScientificBinding


@dataclass(frozen=True, slots=True)
class RunSkeleton:
    """Accepted snapshot and the inputs persisted with it."""

    snapshot: RunSnapshot
    request: RunRequest
    config: ConfigProfileSnapshot


def build_run_admission(
    *,
    config: ConfigProfileSnapshot,
    request: RunRequest,
    scientific_binding: ResolvedScientificBinding,
    config_source: RunConfigSource | None = None,
    samples: tuple[SampleBinding, ...] = (),
) -> RunSkeleton:
    """Create the complete durable state required before execution."""

    return RunSkeleton(
        snapshot=RunSnapshot(
            run_id=new_run_id(),
            scientific_binding=scientific_binding,
            config_content_hash=config_content_hash(config),
            config_source=config_source,
            samples=samples,
        ),
        request=request,
        config=config,
    )
