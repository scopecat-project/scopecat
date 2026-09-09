"""Export the UI-used subset of the daemon's OpenAPI contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from scopecat.kernel.units import UNIT_KINDS, UNIT_SCALE_TO_BASE
from scopecat_server.http.transport import create_app
from scopecat_server.services.application import DaemonApplication

OUTPUT = Path(__file__).parent.parent / ".generated" / "ui-api.openapi.json"

_OPERATIONS = {
    ("/api/v1/author-revisions", "get"),
    ("/api/v1/author-revisions/refresh", "post"),
    ("/api/v1/run-comparison", "post"),
    ("/api/v1/procedures/{procedure_run_id}/cancel", "post"),
    ("/api/v1/experiment-launcher/submit", "post"),
    ("/api/v1/procedures/{procedure_run_id}", "get"),
    ("/api/v1/procedures/{procedure_run_id}/operator", "get"),
    ("/api/v1/procedures/{procedure_run_id}/dispatch", "post"),
    ("/api/v1/experiment-launcher", "get"),
    ("/api/v1/experiment-launcher/preview", "post"),
    ("/api/v1/analyses", "get"),
    ("/api/v1/analyses/{analysis_id}/contents/{selector}/bytes", "get"),
    ("/api/v1/analyses/{selector}", "get"),
    ("/api/v1/config-registry", "get"),
    ("/api/v1/config-registry/contexts", "post"),
    ("/api/v1/config-registry/contexts/resolve", "post"),
    ("/api/v1/config-registry/contexts/structure/preview", "post"),
    ("/api/v1/config-registry/activation-operations", "post"),
    ("/api/v1/config-registry/activation-operations/{operation_id}", "get"),
    ("/api/v1/config-registry/activations", "get"),
    ("/api/v1/config-registry/active", "get"),
    ("/api/v1/config-registry/drafts/preview", "post"),
    ("/api/v1/config-registry/entries/{entry_id}", "get"),
    ("/api/v1/config-registry/publish-operations", "post"),
    ("/api/v1/config-registry/publish-operations/{operation_id}", "get"),
    ("/api/v1/events", "get"),
    ("/api/v1/health", "get"),
    ("/api/v1/instrument-drivers", "get"),
    ("/api/v1/instrument-drivers/probe", "post"),
    ("/api/v1/instruments", "get"),
    ("/api/v1/instruments/{instrument_id}", "get"),
    ("/api/v1/procedures", "get"),
    ("/api/v1/procedures/{procedure_run_id}/steps", "get"),
    (
        (
            "/api/v1/procedures/{procedure_run_id}/steps/{step_key}/"
            "attempts/{attempt}/input"
        ),
        "post",
    ),
    ("/api/v1/reviews", "get"),
    ("/api/v1/reviews/{session_id}", "get"),
    ("/api/v1/reviews/{session_id}/compile", "post"),
    ("/api/v1/instrument-sessions", "post"),
    ("/api/v1/instrument-sessions/{session_id}/abort", "post"),
    ("/api/v1/instrument-sessions/{session_id}/attention", "post"),
    ("/api/v1/instrument-sessions/{session_id}/close", "post"),
    ("/api/v1/instrument-sessions/{session_id}/heartbeat", "post"),
    (
        "/api/v1/instrument-sessions/{session_id}/instruments/{instrument_id}/collect",
        "post",
    ),
    (
        (
            "/api/v1/instrument-sessions/{session_id}/instruments/"
            "{instrument_id}/configured-defaults/apply"
        ),
        "post",
    ),
    (
        (
            "/api/v1/instrument-sessions/{session_id}/instruments/"
            "{instrument_id}/state/apply"
        ),
        "post",
    ),
    (
        "/api/v1/instrument-sessions/{session_id}/instruments/{instrument_id}/state",
        "get",
    ),
    (
        (
            "/api/v1/instrument-sessions/{session_id}/instruments/"
            "{instrument_id}/state/observed"
        ),
        "post",
    ),
    (
        (
            "/api/v1/instrument-sessions/{session_id}/instruments/"
            "{instrument_id}/state/read"
        ),
        "post",
    ),
    (
        "/api/v1/instrument-sessions/{session_id}/instruments/{instrument_id}/invoke",
        "post",
    ),
    ("/api/v1/runs", "get"),
    ("/api/v1/runs/{run_id}", "get"),
    ("/api/v1/runs/{run_id}/failure-evidence", "get"),
    ("/api/v1/runs/{run_id}/measured-costs", "get"),
    ("/api/v1/runs/{run_id}/analyses", "get"),
    ("/api/v1/runs/{run_id}/analyses/{selector}", "get"),
    ("/api/v1/runs/{run_id}/artifacts/{selector}/bytes", "get"),
    ("/api/v1/runs/{run_id}/artifacts/{selector}/json", "get"),
    ("/api/v1/runs/{run_id}/artifacts/{selector}/text", "get"),
    ("/api/v1/runs/{run_id}/attention", "post"),
    ("/api/v1/runs/{run_id}/contents", "get"),
    ("/api/v1/runs/{run_id}/contents/{role}/{content_id}", "get"),
    ("/api/v1/runs/{run_id}/datasets/{selector}", "get"),
    ("/api/v1/runs/{run_id}/execution-segments", "get"),
    ("/api/v1/runs/{run_id}/measurements/live", "get"),
    ("/api/v1/runs/{run_id}/measurements/preview", "get"),
    ("/api/v1/runs/{run_id}/measurements/query", "post"),
    ("/api/v1/runs/{run_id}/measurements/traces/query", "post"),
    ("/api/v1/runs/{run_id}/parameter-proposals", "get"),
    ("/api/v1/runs/{run_id}/point-plan/queue", "get"),
    ("/api/v1/runs/{run_id}/point-plan/queue", "post"),
    ("/api/v1/runs/{run_id}/point-plan/decisions", "get"),
    ("/api/v1/runs/{run_id}/point-plan/resolve", "post"),
    ("/api/v1/runs/{run_id}/records/{selector}/json", "get"),
    ("/api/v1/samples", "get"),
    ("/api/v1/samples/{sample_id}", "get"),
    ("/api/v1/samples/{sample_id}/analyses", "get"),
    ("/api/v1/samples/{sample_id}/analyses/{selector}", "get"),
    (
        "/api/v1/samples/{sample_id}/analyses/{analysis_id}/contents/{selector}/bytes",
        "get",
    ),
    ("/api/v1/samples/{sample_id}/revisions", "get"),
    ("/api/v1/samples/{sample_id}/revisions/{revision}", "get"),
    ("/api/v1/samples/{sample_id}/revisions/{revision}/artifacts", "get"),
}

# Binary endpoints do not reference their decoded models in OpenAPI. The UI still
# needs these types after decoding their response frames locally.
_COMPONENT_ROOTS = {"CollectReceipt"}


def main() -> None:
    unit_output = OUTPUT.parent.parent / "src" / "unit-registry.json"
    unit_output.write_text(
        json.dumps(
            {
                unit: {"kind": kind, "scale": UNIT_SCALE_TO_BASE.get(unit)}
                for unit, kind in sorted(UNIT_KINDS.items())
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    # Route registration only closes over the backend; schema generation never calls it.
    app = create_app(cast("DaemonApplication", object()))
    openapi = app.openapi()
    paths: dict[str, dict[str, object]] = {}
    for path, method in sorted(_OPERATIONS):
        paths.setdefault(path, {})[method] = openapi["paths"][path][method]

    components = openapi["components"]["schemas"]
    # These payloads are intentionally opaque in the console and recursive in JSON
    # Schema, so narrowing them here also keeps the generated contract bounded.
    for name in (
        "RunRequest-Output",
        "pydantic__types__JsonValue",
        "scopecat__kernel__json_types__JsonValue",
    ):
        components[name] = {}
    reachable = _reachable_components(paths, components, roots=_COMPONENT_ROOTS)
    schema = {
        "openapi": openapi["openapi"],
        "info": openapi["info"],
        "paths": paths,
        "components": {
            "schemas": {name: components[name] for name in sorted(reachable)}
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _reachable_components(
    value: object,
    components: dict[str, object],
    *,
    roots: set[str] | None = None,
) -> set[str]:
    pending = [*(roots or ()), *_component_refs(value)]
    found: set[str] = set()
    while pending:
        name = pending.pop()
        if name in found:
            continue
        found.add(name)
        pending.extend(_component_refs(components[name]))
    return found


def _component_refs(value: object) -> set[str]:
    if isinstance(value, dict):
        ref = value.get("$ref")
        found = (
            {ref.removeprefix("#/components/schemas/")}
            if isinstance(ref, str) and ref.startswith("#/components/schemas/")
            else set()
        )
        for item in value.values():
            found.update(_component_refs(item))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for item in value:
            found.update(_component_refs(item))
        return found
    return set()


if __name__ == "__main__":
    main()
