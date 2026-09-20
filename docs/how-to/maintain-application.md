# Maintain a local application

Use the application manager to inspect and maintain registered experiment services.
Run `lab.cmd --manage` on Windows or `python lab.py --manage` from the original
installation directory. A source installation uses `scopecat app --manage` with the same
`--home` and `--source` options used when it was registered.

Ordinary launch opens the last successfully selected experiment workbench (or the
sole service before the first choice), starting/checking its existing runtime.
It can initialize configured instruments, but never submits or resumes a measurement.
Use `--manage` for maintenance without starting a service. Explicit `scopecat app
PROJECT` selects a different primary workbench after a successful check. An absent
remembered registration returns to management instead of selecting another service.

The workbench's **Help and maintenance** page links to this guide. Returning to the
manager does not change a notebook's scientific selection or start a measurement.

## First use

A fresh installation opens **Set up the primary workbench (设置主要实验工作台)**.
Choose **Create** for an ordinary virtual experiment project, or **Connect** for
an existing laboratory code directory containing `scopecat.toml`. The directory
is the primary editable code source, including its laboratory capability declarations;
it is not an independently installed adapter package. Use a trusted directory
prepared by your laboratory.

Enter the code directory and optionally a separate, new data directory. Leaving
data blank uses the project's default location for creation and preserves the
existing binding for connection. Setup never relocates a retained store or replaces
an existing code directory. The generated project includes a virtual instrument
example and an authored signal experiment, not a teaching sandbox.

Choose **Create / connect and open**. Setup checks the GUI and selected environment,
registers the directory, starts its existing service policy, and opens the workbench
in the same tab. Starting a laboratory deployment may initialize instruments;
no measurement is submitted or resumed. Successful setup remembers the primary
workbench for the next ordinary launch.

A project `.venv` is used when present; a broken one is an error. Without it, setup
uses the installed application's environment. This flow does not install laboratory
dependencies, choose vendor SDK versions, or supply private machine settings. The
laboratory must prepare those requirements. GUI assets come from the public
installation; a source host uses its built `apps/scopecat-ui/dist`.

Errors remain in **Recent operations** and its log. A failed registration or startup
retains the created files; fix the reported environment and use **Connect** on the
same directory, or restart its registered service. Reopening the page does not replay
setup. To add another directory later, expand the setup form in management.

## Identify the environment before changing it

Expand **Project and environment (项目与环境)** on the service card. It shows the
registered project directory, Python interpreter, GUI directory, environment prefix
and package versions. These values come from the last successful registration or
recheck; they are not a live package inventory. Keep the original installation
launcher and these locations available to the next maintainer.

| Location | What it identifies |
|---|---|
| Project directory | The existing experiment service and its deployment configuration |
| Python interpreter | The environment used to execute that service; preserve its virtual-environment path |
| GUI directory | The workbench assets selected for the service |
| Manager home | The manager's service catalog and operation history, selected by its original launcher or `--home` |

Scientific data may live outside the project directory. Check the workbench Help
page and project configuration, and use the [backup guide](backup-and-restore.md)
before maintenance that affects retained data.

## Install a newer fixed delivery

For an offline fixed delivery, use its `install.py` with the original manager home.
For example, from the new delivery directory in PowerShell:

```powershell
python install.py --home "D:\Scopecat-Lab"
```

Replace the example home with the existing installation directory. Do not create
another numbered home just to update the manager. Use a delivery matching the
computer's operating system, CPU and Python ABI.

The installer prepares a retained release and verifies its installed entry before
selecting it in `lab.py`. Installation into one home is serialized. Copy or package
installation failures leave the previous default entry usable. Run the same command
again after resolving the reported cause; an incomplete managed runtime is preserved
as a failed attempt and rebuilt at its final path. A damaged complete delivery or
mismatched receipt is reported instead of being silently replaced. Existing releases,
project files and tutorial copies are not deleted.

Reopen `lab.cmd` / `lab.py` after installation. Preparing and selecting a delivery
is separate from replacing the running manager: active management work blocks that
replacement. Let the operation finish, inspect its result, then reopen the launcher.
The manager retains its service catalog and operation history. Replacement does not
stop experiment services or replay measurements.

A new manager delivery does not redirect existing registered services to its Python.
Their registered interpreter and GUI paths remain authoritative. Use the workflows
below when intentionally updating those environments; new tutorial copies use the
selected delivery while old copies retain their own environment.

## Recheck after updating the existing environment

1. Finish measurements and close notebook connections. In the manager choose
   **Stop service (停止服务)** and wait for **Stopped (未启动)**.
2. Update the environment using the installation's tested package or delivery
   procedure. The manager does not install packages. Keep the same project,
   interpreter and GUI paths for this recheck workflow.
3. Choose **Recheck environment (重新检查环境)**. It probes the registered
   interpreter, project and GUI, then records the validated environment identity.
   The service number, name, directories and scientific records are retained.
4. Wait for the operation to succeed. Inspect **Project and environment** and the
   operation log. The service remains stopped.
5. Choose **Start / check workbench (启动 / 检查工作台)** when ready, then open the
   separate workbench tab. Starting may initialize configured instruments; it does
   not replay a measurement. Preview new work before submitting it.

Rechecking also works when nothing changed. It never starts the service, selects
an executable setup, changes a working point or grants calibration acceptance.
The version check does not attest every dependency or editable source file.

## If the paths changed

A new interpreter or GUI directory requires local registration with those explicit
paths. Stop the old service first. Run the command in the manager's installed
environment. The setup form selects a code directory and optional initial data
location; explicit interpreter and GUI overrides remain a maintainer CLI operation.

For example, in PowerShell:

```powershell
scopecat app "D:\Lab\experiment" --manage --python "D:\Lab\runtime\Scripts\python.exe" --static-dir "D:\Lab\gui\dist" --home "D:\Scopecat-Lab" --name "Experiment service"
```

Replace each example location with the recorded local location; these are not
universal Windows defaults. `--home` must select the original manager catalog.
Use the existing display name with `--name`. Omit `--static-dir` only when the
target environment provides the packaged GUI you intend to use. For a source
manager, also retain its original `--source` option.

The `--manage` option in this example leaves the service stopped after registration.
Omitting it starts/checks the registered service and opens its workbench.

Registering the same canonical project in the same manager home retains its
service ID. Moving a project or choosing another manager home is a distinct
registration, not an automatic installation transfer. Fixed tutorial deliveries
retain their own launchers and environments; see
[teaching sandboxes](../tutorials/teaching-sandboxes.md) for those disposable copies.

## Resolve a failed check

Open the failed operation's log before changing anything else. A failed recheck
keeps the previous registration and files, and does not fall back to another
interpreter.

- **Service is running or its state is uncertain:** finish active work and resolve
  the service lifecycle first. Rechecking only proceeds from a confirmed stopped
  state; it does not kill or guess ownership of an unknown process.
- **An update happened before stopping:** restore the original environment or use
  its actual runtime to explicitly stop the service with `scopecat stop PROJECT`.
  Then recheck. Do not remove the registration as a way to stop a process.
- **Interpreter, project or GUI is missing:** restore the registered files, or use
  local registration for the intended replacement paths once the service is stopped.
- **Another management operation is active:** wait for its result and inspect its
  log. Interrupted operations retain their evidence and are not replayed on restart.

Removing a stopped registration only removes its manager entry. It does not delete
the project, scientific records or operation history. No supported persistent-data
upgrade baseline is implied by a successful environment check.
