# Organize continuous research history

A research project is a named association inside the persistent data space. It is
separate from a Python source workspace, a physical sample, and the current
parameter selection. Multiple research projects can reference one sample or run;
no payloads are copied or moved.

In the console, open **History**. Create a project with a readable name, select a
sample, and choose **Associate sample**. Sample membership describes participation;
it does not automatically include every past or future run on that sample.
Select **Show runs outside this project to associate**, review the results, and
choose **Associate run**. Clear that checkbox to browse only associated runs.

Filter by sample, working point, bench deployment identity or a time interval.
The end time is exclusive. The console displays local time and sends timestamps
with their offset. New admitted runs retain the deployment that accepted them.
Older data without that evidence remains **unknown**; the current bench is never
invented as its origin.

Open a run to inspect its original measurements, exact configuration and source,
and separately published analyses in the existing run detail view. Renaming a
project or removing an association changes only organization. It never deletes
the run or makes another bench adopt its parameter values. Concurrent project
renames are checked against the selected revision; reload the selection if another
session changed it.

For Python automation through an author session:

```python
from scopecat.records.research_project import ResearchProjectEdit, RunHistoryFilter

project = author.save_research_project(
    "resonator-study", ResearchProjectEdit(name="Resonator study")
)
author.associate_research_member(project.id, "samples", "chip-1")
author.associate_research_member(project.id, "runs", run_id)
page = author.list_runs(
    sample_id="chip-1",
    history=RunHistoryFilter(research_project=project.id, working_point="cooldown-1"),
)
```

Project, membership and run queries use bounded pages. Continue with the returned
cursor (`before` for projects/runs, `after` for memberships). Use
`ResearchProjectEdit(expected_revision=project.revision, name=...)` to rename,
and `associate_research_member(..., present=False)` to detach an association.
Stable IDs remain unchanged when display names change.

This feature uses schema 71. Opening an older store never migrates it implicitly.
Retain its matching reader and follow [backup and restore](backup-and-restore.md).
Shared names or column layouts do not establish scientific comparability across
runs: units, method, configuration and conditions still need explicit review.

For acquisition numbering independent of these associations, see
[record collections](record-collections.md).
