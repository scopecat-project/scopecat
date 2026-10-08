# Ordinary author entry and recovery observation

This bounded software evaluation starts from **Settings → Author code → New code
folder**, following [First experiment](../getting-started/quickstart.md). Baseline:
`5e8077d5818038e17648b8b9e51580e0267edd3e` (#928), including #927. Environment:
Linux x86-64, Python 3.14.7, Chromium and an independent real ipykernel. It is not
native window/editor, unfamiliar-user, installed release or device acceptance.

## Entry finding and repair

At baseline, the ordinary starter `notebooks/02_edit_scan.py` had one `# %%`
execution block containing preparation, submission, analysis and reopen. It did
not display the preview. Running its cells in order therefore did not provide the
quickstart's promised stop between preview and explicit acquisition.

The starter now separates Imports, Connect, Preview, Submit, Result and analysis,
Reopen, and Close using the same author session and receipt APIs. A fresh kernel
can run Imports/Connect, select the existing receipt path, then Reopen/Close.
It does not need to repeat Submit or analysis. Existing generated user folders
are not overwritten. The API guide now distinguishes prepared laboratory folders
from first-use setup; the quickstart points saved-run inspection to **Runs**.

## Ordinary-loop evidence

The maintained [verifier](../../scripts/verify_ordinary_author.py) uses Settings
and real `DesktopAPI` calls from empty application data. No scaffold, registration,
parameters or setup are inserted before this entry. It uses a development
wheel/toolchain payload for real client and execution environment preparation;
native picker/navigation and VS Code activation/`__file__` injection are the
explicit substitutions. See [commands](test-feedback.md#ordinary-settings-author-entry).

The baseline Settings preparation succeeded, including refusal to overwrite an
existing folder and rejection/retry of a missing execution interpreter. The
verifier then failed with `Starter combines preview, submission and results in
one cell`, establishing the material gap rather than an environment failure.

After the cell repair, the observed run was
`run_20261008T061829Z_942d961f`. Preview and a rejected syntax edit left zero runs.
Changing `return scale / ...` to `return 2 * scale / ...`, refreshing and explicitly
submitting produced one run with `[1, 2, 1]`, checked in the real kernel and GUI
measurement table. Application and kernel restart reopened the same receipt and
same GUI values with run count still one. The verifier retains executed cells,
source/payload identity, result JSON and screenshots locally; temporary outputs
are not a published artifact or native acceptance record.

## Separate experiment-form observation

These are observed deficiencies, **not passing recovery acceptance**. They were
checked without changing production storage, permissions, identity or data owners.
The completed ordinary folder above supplied the source and initial parameter
branch. For this additional form audit only, its analytic setup definition was
saved as `audit-bench` through the existing author setup API, then selected with
the `starter` branch in Experiments. This supplemental setup does not establish
the Settings entry.

| Action | Observed outcome |
| --- | --- |
| Set Center `0.25`, Position `0.75`, preview, then change only source body/comment and Refresh author code | Values retained; old preview invalidated; Start disabled until fresh preview |
| Change Position's declared default from `0.0` to `0.5`, then Refresh author code | Center `0.25` retained; Position `0.75` replaced with default `0.5` |
| Leave Center empty and Position scan text `-1, nope, 1`; advance the `starter` parameter branch and explicitly adopt its new version | Raw inputs retained; fresh preview required |
| Navigate Configuration → Experiments with that invalid/intermediate input | Raw input retained in this window |
| Reload the page | Raw inputs lost; declaration defaults return and configuration selection resets |
| Set a valid unsubmitted Center `0.75`, restart application and reopen Experiments | Value lost; default `0` returns |
| Admit a GUI submission but abort its response, then restart application/window | Original request panel and input lost; admitted exact procedure and run remain in Retained procedures; no automatic resubmission |

For the last check a Playwright route called the real submit endpoint with
`route.fetch()`, retained its returned `procedure_id` and the outgoing
`request_key`, then used `route.abort("connectionfailed")`. Before restart,
**Check original submission** was available and Start remained disabled. After
restart, that original-request panel was absent. Looking up the exact request key
returned exactly one matching procedure; selecting its timestamped history entry
and its retained-run link opened a Succeeded run. This did not match on mutable
input similarity or recompile/resubmit the request.

Observed request: `2a06ed7c-26a5-42c9-ac9b-c34e5f5d201c`; procedure:
`procedure-b02bf33211804305948c474adb59e148`. The audit home had two runs before this
explicit submission and three afterward, unchanged by restart/history inspection.
These are disposable synthetic test identities, not user data.

`LaunchDraft.tsx` holds both the editable draft and submission attempt in React
state. Its control-definition change path constructs fresh control defaults.
These observations are consistent with those boundaries; they do not demonstrate
a bug in application-owned parameter or Decision draft persistence.

## Completion boundaries

| Status | Scope and evidence |
| --- | --- |
| Existing | [#916](https://github.com/scopecat-project/scopecat/pull/916): ordinary lost response and in-window navigation, plus parameter-draft/restart-conflict coverage; preseeded author source does not prove Settings entry |
| Existing | [#921](https://github.com/scopecat-project/scopecat/pull/921), [#926](https://github.com/scopecat-project/scopecat/pull/926): exact retained-run/publication reads in a fresh kernel |
| Existing | [#928](https://github.com/scopecat-project/scopecat/pull/928): shipped Help editing lesson syntax repair/restart Continue, distinct from ordinary New code folder |
| This change | Real Settings preparation/retries, ordinary starter cell repair, edited source, preview/explicit acquisition, GUI exact values and application/kernel restart without reacquisition |
| Observed residual | Experiment raw-input persistence, control-declaration changes replacing edits, and original GUI submission receipt loss across restart |
| Not evaluated | Multi-window experiment edit conflicts, removed/renamed fields, cancellation/retention policy, crashes before server admission, preparation interrupted midway, actual native picker/editor behavior |
| Outside this slice | New draft data types/contracts, permissions, hardware, distribution/release, private pins and old device/example retirement |

The [recoverable-editing contract](architecture/draft-recovery.md) requires
preserving valuable intermediate input and its original baseline. Implementing
experiment drafts needs a deliberate logical target, baseline/revalidation,
conflict, retention and completion policy. Uncertain submission additionally
needs the original exact request identity retained before sending, independently
of an editable draft, with recovery unable to authorize acquisition. Decide these
type-specific lifecycle choices before adding persistence. This change does not
introduce a generic draft system, browser-storage workaround or new session/client.
