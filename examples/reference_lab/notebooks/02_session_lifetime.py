"""Capture a terminal run, close its session, and reattach without acquisition."""

from __future__ import annotations

# %%
import scopecat as sc

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.notebook import show
from reference_lab.workflows.temperature_diagnostic import temperature_diagnostic

# %%
project = sc.open_project(EXAMPLE_ROOT)
with project.connect(operator="gallery") as first_session:
    run = first_session.run(temperature_diagnostic(), name="Session lifetime sample")
    snapshot = run.snapshot
    retained_records = run.measurements().records
    lazy_measurements = run.measurements()

# %%
# These are detached values. The old run and lazy_measurements still belong to
# the closed first_session; reading them would require its closed connection.
show({"run_id": snapshot.run_id, "status": snapshot.status})

# %%
with project.connect(operator="gallery") as second_session:
    reattached = second_session.get_run(snapshot.run_id)
    reattached_records = reattached.measurements().records
    reattached_snapshot = reattached.snapshot

session_lifetime_summary = {
    "run_id": snapshot.run_id,
    "status": snapshot.status,
    "same_snapshot": reattached_snapshot == snapshot,
    "same_measurements": reattached_records == retained_records,
    "records": len(reattached_records),
    "sessions_closed": first_session.is_closed and second_session.is_closed,
}
show(session_lifetime_summary)
