"""HTTP exit admission: ordinary writes stop, existing execution can settle."""

import re
from typing import cast

from fastapi import Request
from scopecat.automation.effects import PROCEDURE_EFFECT_HEADER, RETAINED_EFFECT_HEADER
from starlette.concurrency import run_in_threadpool

from scopecat_server.services.exit_drain import ExitDrain
from scopecat_server.services.project_workers import ProjectProcedureWorkers


async def drain_allows(
    request: Request, drain: ExitDrain, workers: ProjectProcedureWorkers
) -> bool:
    path = request.url.path.removeprefix("/api/v1/")
    # POST queries used by already admitted work do not launch execution.
    if path in {
        "parameters/resolve",
        "config-registry/contexts/resolve",
        "instrument-contracts/resolve",
        "measurement-context/resolve",
        "calibration-checks/query",
        "calibration-checks/report",
        "procedures/runnable/query",
        "experiment-plans/read",
        "apparatus-objects/resolve",
        "measurement-targets/resolve",
    }:
        return True
    # Existing executor operations are independently fenced by their service.
    # Attention resolution is a human decision, never shutdown cleanup.
    if re.fullmatch(
        r"runs/[^/]+/(executor/(start|heartbeat)|cancel|hardware/(execute|finish)|"
        r"instruments/.*|measurements/.*|payload-objects/.+|terminal|host-parameter-evidence|"
        r"coverage/advance|recovery-groups|domain-jobs/transitions|point-plan/.*)",
        path,
    ):
        return True
    if re.fullmatch(
        r"procedures/[^/]+/(worker/lease/(heartbeat|release)|steps/begin|"
        r"steps/.+/attempts/\d+/(complete|fail|attention|input/wait|resources/wait)|"
        r"attention|cancel|close)",
        path,
    ):
        return True
    if re.fullmatch(r"procedures/[^/]+/worker/lease/acquire", path):
        identity = path.split("/")[1]
        return not await run_in_threadpool(
            drain.deferred_procedure, identity
        ) or not await run_in_threadpool(workers.process_settled, identity)
    if re.fullmatch(r"instrument-sessions/[^/]+/(heartbeat|close|abort)", path):
        return True
    if re.fullmatch(r"reviews/[^/]+/worker/(claim|heartbeat|complete|close)", path):
        return True
    if path.startswith("application-open/host/") or re.fullmatch(
        r"(author-preparations/[^/]+/cancel|analysis-follows/[^/]+/stop|"
        r"procedure-schedule-cancellations/.+)",
        path,
    ):
        return True
    # Necessary children retain the existing exact parent/step/hash validation.
    if path == "runs":
        try:
            body = cast("object", await request.json())
        except ValueError:
            return False
        return (
            isinstance(body, dict)
            and cast("dict[str, object]", body).get("procedure_child") is not None
        )
    # Publications can sit outside a task URL. Only a live durable effect may
    # publish here during drain; the header cannot admit tasks or answer input.
    retained = request.headers.get(RETAINED_EFFECT_HEADER)
    publication = re.fullmatch(r"runs/([^/]+)/analyses", path)
    if (
        retained
        and publication
        and drain.retained_publication(retained, publication[1])
    ):
        return True
    effect = request.headers.get(PROCEDURE_EFFECT_HEADER)
    if effect and (
        path
        in {
            "analyses",
            "parameters/branch-publications",
            "parameters/branch-commits",
            "parameters/revisions",
        }
        or re.fullmatch(
            r"runs/[^/]+/(analyses|attachments|parameter-compositions|payload-objects/.+)",
            path,
        )
        or path.startswith("payload-objects/")
        or re.fullmatch(r"samples/[^/]+/analyses", path)
    ):
        return await run_in_threadpool(drain.live_effect, effect)
    return False
