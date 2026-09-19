# Windows local application trial

This is a short human check for [#616](https://github.com/scopecat-project/scopecat/issues/616),
focused on installation and everyday entry. It does not repeat the parameter and
compute lessons already tried. No AI assistant, repository checkout, Node or local
Scopecat build is required. Use only a disposable application home and synthetic
practice; this trial does not qualify physical instruments or historical-data upgrades.

## Delivery handoff — pending

This handoff is tracked in [#668](https://github.com/scopecat-project/scopecat/issues/668).
The manual **Full acceptance** workflow has a `local-application` profile for
installed and browser checks; it is not the full qualification profile.
Do not start from an arbitrary latest download. The maintainer must supply the
following before asking someone to run this trial:

| Item | Required handoff |
|---|---|
| Download | Successful run link; `scopecat-tutorial-windows-latest` artifact |
| Identity | Commit, bundle build identity, OS/CPU/Python ABI |
| Automated evidence | Windows/Linux results, including any failed or deferred checks |
| Update pair | Two explicitly identified, trusted deliveries, if checking a version change |

The exact run and revision are not yet recorded here. A merged PR or a green fast CI run is not
an installed qualification result. Actions artifacts expire; retain the accepted
delivery separately. In the Windows artifact, `tutorial/` is the delivery and
`tutorial-evidence/` contains automated reports. The delivery must contain
`install.py`, its manifest and all payload files. Keep that folder intact after extraction. Use a matching Python
3.14 installation and uv; opening a teaching Notebook also needs VS Code with its
Python/Jupyter extensions. Ask for a corrected handoff if a prerequisite or matching
artifact is missing rather than rebuilding the project.

## 1. Install into a path you can recognize

Choose one new disposable home with spaces and Chinese characters, for example
`D:\Scopecat 体验`. Use a drive that exists on this computer. In File Explorer,
open the extracted delivery folder and choose **Open in Terminal**. Run:

```powershell
python install.py --home "D:\Scopecat 体验"
```

The quoted path is your chosen installation home, not the extracted download.
Replace it once with your actual path and record it. This is the only installation
command needed; the installer selects managed release directories. Do not activate
an environment or invent numbered folders. Save the installer's final message,
including the selected version identifier. If `python` opens the Store or runs
another version, report that prerequisite problem before continuing.

## 2. Open and reopen the application

In that home, double-click `lab.cmd`. Expect the local management page, with
experimental services as the main view and teaching under Help. A new home can
have no registered services. Close the browser tab, double-click the same
`lab.cmd` again, and check that it reconnects without asking for a port, environment
or project number. Note any Windows prompt, disappearing terminal, duplicate
page or unclear wait; these are useful human findings even if a retry works.

Expand **Help · Teaching and practice**, open one topic you already know, and
check that its prepared folder opens in VS Code. There is no need to repeat the
lesson or modify experiment code. Close its Notebook kernel, stop the exercise
service from its card, then reopen the manager and check that the copy and its
operation history remain visible. Do not reset or delete the copy for this check.

## 3. Reinstall into the same home

Run the same install command again from the same delivery. Reopen the original
`lab.cmd`; the existing practice copy and operation history should still be there.
There should be no request to rebuild the GUI or create another home.

Only if the maintainer supplied a verified update pair, repeat installation from
the second delivery into that same home, after management operations have finished.
Reopen the original launcher and record the newly selected version identifier.
Old practice copies remain attached to their original environments. A manager
update does not update registered experiment-service environments; do not infer
that their package versions changed. Without an update pair, mark version-change
behavior **not attempted**, rather than treating same-delivery reinstallation as
an upgrade test.

If a virtual experiment service was explicitly supplied with the handoff, also
check **Stop service → Recheck environment → Start / check workbench**. Recheck
must leave it stopped and retain its service number; opening its workbench should
leave the manager available in its original tab. Follow **Help and maintenance**
from that workbench and judge whether the registered project, interpreter and GUI
locations are understandable. Otherwise mark this browser task **not attempted**;
do not substitute a real instrument project or construct a registration yourself.

## Report the result

Send the commit/build identity, Windows version, actual home path, and a short
result for each attempted step. Record how long an unclear wait lasted and which
wording or location caused hesitation. A few sentences and screenshots are enough;
no JSON report or command transcript is required for successful steps.

For a failure, retain the error text, screenshot and operation log offered by the
manager, plus whether the original launcher still works. For installation or
pre-browser failure, retain terminal output instead. Do not delete the home,
failed runtime or downloaded delivery to make the error disappear, and do not
spend time repeatedly retrying an unexplained failure. Redact local information
before sharing logs. The maintainer will narrow any additional diagnostic task.

Software checks, these human observations and future physical-device evidence are
separate results. Successful steps do not designate a persistent-data baseline.
