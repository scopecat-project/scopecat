"""Inspect the precise diagnostic for overlapping work on one drive channel."""

from __future__ import annotations

# %%
import scopecat as sc
from scopecat_quantum.pulses import PulseValidationError

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.notebook import show
from reference_lab.workflows.ramsey_experiments import conflicting_drive

# %%
try:
    with sc.open_project(EXAMPLE_ROOT).connect(operator="gallery") as lab:
        lab.preview(conflicting_drive.build())
except PulseValidationError as error:
    conflict_codes = [issue.code for issue in error.issues]
    conflict_messages = [issue.message for issue in error.issues]
else:
    raise AssertionError("overlapping q0 drive branches must be rejected")

channel_conflict_summary = {
    "codes": conflict_codes,
    "mentions_drive_q0": any(
        "('drive', 'qubit', 'q0')" in message for message in conflict_messages
    ),
}
show(channel_conflict_summary)
