# Working-point cohort automation is retired

The former `scopecat automation work --working-point ...` workflow and application
cohort/publication registries are retired development interfaces. They coupled
scientific scope, parameter history and automatic freshness to a saved full
configuration. New workers do not enroll or drain these cohorts automatically.

Use [parameter-branch calibration procedures](automate-parameter-calibration.md).
Each request freezes its target list, scientific subject, exact parameter/setup
inputs and destination branch. Joint verification precedes atomic publication;
recovery reuses the original request and retained receipt.

Existing scientific data is not rewritten or deleted. Retain an old environment
if needed for archival reading. Low-level legacy cohort services remain in the
framework pending a separate review; this does not make them a supported new
application deployment path. Automatic scientific freshness needs a new explicit
applicability policy and is not inferred by the replacement worker.
