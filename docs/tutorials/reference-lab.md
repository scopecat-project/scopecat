# The reference gallery is retired as teaching material

Use [runnable tutorial sandboxes](teaching-sandboxes.md) for parameters, compute,
source refresh and grouped analysis, or [starter authoring](starter-authoring.md)
for the small virtual-instrument project. New advanced lessons will use focused
sandboxes and current public APIs; device and calibration topics are not yet
fully covered by those lessons.

The old reference scripts are retained only where integration tests still need
their device or scientific behavior. Their application assembly, default-config
workflow and author interfaces are not recommended patterns or compatibility
commitments. Do not copy them into new laboratory projects.

Maintainers should use the [retirement inventory](../development/reference-gallery-retirement.md)
to decide which behaviors need replacement tests and which old assumptions should
be removed. Keeping a necessary integration test does not require preserving its
original notebook or API design.
