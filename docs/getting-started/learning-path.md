# Choose a learning path

For a complete runnable Notebook and automatic environment setup, start with
[tutorial sandboxes](../tutorials/teaching-sandboxes.md). Each topic is independent and disposable.

Learn one laboratory task at a time. Installation and a working project are the
starting conditions for authoring lessons; instrument integration and framework
internals are separate roles, not prerequisites for writing an experiment.

## Experiment users and authors

| Task | Start here | Check your understanding |
| --- | --- | --- |
| Retain a first result | [Pilot quickstart](quickstart.md) | Restart and find the same run without acquiring again. |
| Change an existing experiment | [Starter authoring](../tutorials/starter-authoring.md) | Change a parameter and scan range; explain which operation starts acquisition. |
| Calculate and return data | [Compute and typed reads](../how-to/compute-and-read.md), [write an experiment](../how-to/write-an-experiment.md) | Change one returned value and read it; distinguish local array axes from scan axes. |
| Read and select data | [Measurement data](../how-to/use-measurement-data.md) | Plot selected points and reopen their retained values in a new session. |
| Analyze retained measurements | [Ordinary analysis](../guides/ordinary-analysis.md) | Change an analysis while retaining the original measurement and its previous analysis. |
| Edit, diagnose and recover | [Refresh author code](../how-to/refresh-author-code.md), [resume interrupted runs](../how-to/resume-interrupted-runs.md) | Correct one error and identify the failed and new jobs; learn which work supports resume. |
| Compare and calibrate | [Retained comparisons](../how-to/retained-run-comparison.md), [verify candidates](../how-to/verify-parameter-candidates.md) | Validate a candidate before using it in subsequent experiments. |

Basic refresh, error reading and reopening belong in the first editing lesson.
Advanced recovery can wait until needed. After individual tasks, combine a short
sequence: edit a calculation, refresh, run, fix an intentional error, and reopen
both old and new results in a new session. Separate successes do not establish
that these transitions are understandable.

These are learning objectives, not a claim that every route has completed human
usability evaluation. Each linked guide describes its current API and limits.

## Laboratory maintainers

Use the quickstart's build section, [project layout](../reference/project-layout.md),
[configuration management](../how-to/manage-configuration.md), and
[backup and restore](../how-to/backup-and-restore.md). Practice restoring into a
new location and reading results. An archive that has never been restored is not
that exercise's completion condition.

For each command, identify its working directory, the project or artifact path to
replace, its effect and the observable completion state. Author lessons should
start from an environment the maintainer has prepared. No AI assistant is required
for either role.

## Extension and framework developers

[Instrument extensions](../extensions/instruments.md) cover integrating devices;
[quantum extensions](../extensions/quantum.md) cover domain-specific programs and
targets. The [reference lab](../tutorials/reference-lab.md) is an integration
example, not the first project template. Follow the
[development documentation](../development/index.md) only when changing Scopecat
itself.

## Find the right kind of explanation

Tutorials walk through a complete task. How-to guides answer a specific operational
question. [Concepts](../concepts/index.md) explain execution, identity and data
semantics; [reference](../reference/index.md) gives precise API and CLI details.
Return to those explanations when a task needs them instead of reading every
framework concept before the first run.
