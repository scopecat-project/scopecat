"""Bounded execution interpreter for admitted single-source work declarations.

Only ``begin_attempt`` precedes the external provider. The explicitly declared
choice of the sole candidate and ready-group publication may share the result
transaction. Staged mode keeps the original four-transaction path for comparison.
"""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from typing import TYPE_CHECKING

from .core import Conflict

if TYPE_CHECKING:
    from .core import Store

Provider = Callable[[str, dict, dict[str, dict]], list[dict]]
FaultHook = Callable[[str], None]
PublicationHook = Callable[[str, str], None]


def drain(
    store: Store,
    fence: int,
    provider: Provider,
    *,
    mode: str | None = None,
    fault: FaultHook | None = None,
    on_publish: PublicationHook | None = None,
) -> dict:
    """Run currently admitted units in their persisted traversal order.

    No implicit membership closure, dynamic proposal, retry authorization or
    result-choice inference occurs here. Open groups can receive selected
    results but remain unpublished until an explicit closure. A newly closed
    group with already selected results, including a closed zero-member group,
    can be published without provider work.
    """
    state = store.state()
    author = state.get("author_intent")
    if not isinstance(author, dict):
        raise Conflict("execution requires an admitted author intent")
    admitted_mode = author.get("commit_mode")
    mode = admitted_mode if mode is None else mode
    if mode not in {"atomic", "staged"} or mode != admitted_mode:
        raise Conflict("execution commit mode differs from admitted author intent")
    if author.get("intent", {}).get("selection_policy") != "sole_candidate":
        raise Conflict("execution requires an explicit sole-candidate selection policy")
    started = perf_counter()
    executed: list[str] = []
    published: list[str] = []
    first_publication = None

    def observe_publications(snapshots: dict[str, str]) -> None:
        nonlocal first_publication
        for group_id, snapshot_id in snapshots.items():
            published.append(group_id)
            if first_publication is None:
                first_publication = perf_counter() - started
            if fault is not None:
                fault("group-publish")
            if on_publish is not None:
                on_publish(group_id, snapshot_id)

    # This invocation handles this admitted extent only. Appends require a new
    # drain call, making the adaptive decision boundary visible to the caller.
    ordered_units = [unit["id"] for unit in state["units"]]
    for unit_id in ordered_units:
        state = store.state()
        unit = next(unit for unit in state["units"] if unit["id"] == unit_id)
        groups = tuple(
            group["id"]
            for group in state["groups"]
            if unit_id in group["unit_ids"] and group["published_snapshot"] is None
        )
        selected = next(
            (item for item in state["selections"] if item["unit_id"] == unit_id), None
        )
        if selected is not None:
            if not any(state["readiness"][group_id]["ready"] for group_id in groups):
                continue
            snapshots = store.select_and_publish(
                fence, unit_id, selected["bundle_hash"], publish_groups=groups
            )
        else:
            saved = [item for item in state["results"] if item["unit_id"] == unit_id]
            if len(saved) > 1:
                raise Conflict(
                    "multiple saved candidates require an explicit result decision"
                )
            if saved:
                if mode == "atomic":
                    snapshots = store.select_and_publish(
                        fence, unit_id, saved[0]["hash"], publish_groups=groups
                    )
                else:
                    store.select_result(fence, unit_id, saved[0]["hash"])
                    snapshots = _publish_ready(store, fence, groups)
            else:
                attempt_id = store.begin_attempt(fence, unit_id)
                outputs = provider(
                    attempt_id, unit, {point["id"]: point for point in state["points"]}
                )
                if fault is not None:
                    fault("provider-return")
                provenance = {
                    "executor": "result-ledger.intent-interpreter.v1",
                    "attempt_id": attempt_id,
                }
                if mode == "atomic":
                    receipt = store.complete_attempt(
                        fence,
                        attempt_id,
                        outputs,
                        provenance,
                        selection_policy=unit["selection_policy"],
                        publish_groups=groups,
                        fault_hook=fault,
                    )
                    snapshots = receipt["snapshot_ids"]
                else:
                    bundle = store.save_result(fence, attempt_id, outputs, provenance)
                    if fault is not None:
                        fault("result-save")
                    store.select_result(fence, unit_id, bundle)
                    snapshots = _publish_ready(store, fence, groups)
                executed.append(unit_id)
        observe_publications(snapshots)
    # Closure is a scientific commitment independent of work cardinality. A
    # zero-proposal group has no unit through which the loop could publish it.
    # Read current readiness so already-published groups cannot notify twice.
    state = store.state()
    for group in state["groups"]:
        readiness = state["readiness"][group["id"]]
        if readiness["ready"] and readiness["published_snapshot"] is None:
            snapshot_id = store.publish_group(fence, group["id"])
            observe_publications({group["id"]: snapshot_id})
    return {
        "executed_units": executed,
        "published_groups": published,
        "first_publication_seconds": first_publication,
    }


def _publish_ready(
    store: Store, fence: int, group_ids: tuple[str, ...]
) -> dict[str, str]:
    """The deliberately staged comparison retains separate publication commits."""
    return {
        group_id: store.publish_group(fence, group_id)
        for group_id in group_ids
        if store.readiness(group_id)["ready"]
    }
