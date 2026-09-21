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

## Open an already bound author folder

After a maintainer has registered an author workspace with the existing laboratory,
open its workbench without registering another service:

```shell
scopecat app --workspace "/path/to/author code" --home "/path/to/application home"
```

The application resolves the folder's existing source identity and laboratory owner,
uses the owner's registered interpreter/GUI/settings, and follows the normal durable
service start checks. Startup may initialize instruments; opening never submits a
measurement. The workbench selects that source before loading its experiment catalog.
The laboratory must already be registered in this application home. Unknown sources,
missing owners and interpreter/binding mismatches fail without creating or rebinding
a service. A retained but unavailable source stays unavailable in the workbench; it
never silently selects the service owner's code.

`--workspace` cannot be combined with a positional project or `--python`, `--name`,
`--static-dir`: those options change deployment registration. `--manage` and
`--no-browser` retain their maintenance/state-only behavior without starting a service.
The remembered primary choice is still the laboratory, not a global code selection;
opening a source is page-local. Subsequent launch without `--workspace` opens that
laboratory with its normal default code selection.

This reuses [registered same-environment sources](../development/architecture/workspace-bindings.md).
It does not yet install an environment or register a source automatically.
Independent deployments still
have separate hardware authorities; this entry does not merge them.

## Separate author code from an installed laboratory

A laboratory using an installed adapter can bind author folders that contain no
`[lab]` declaration. The maintained laboratory directory holds the runtime and its
adapter selection:

```toml
[lab.adapter]
distribution = "my-laboratory-adapter"
manifest = "my_lab/adapter.toml"

[authors]
dependencies = []
```

An independent author folder needs only its source and `scopecat.toml`:

```toml
[authors]
modules = ["experiments"]
source_roots = ["src"]
refresh_roots = ["src"]
dependencies = []
```

Put experiment and analysis code in `src/experiments.py` or `src/experiments/`.
List any additional author dependencies in `dependencies`; the selected adapter's
packages are inherited automatically. Use the laboratory's installed environment
for both registration and Notebook execution. Stop its service, then register:

```shell
scopecat register-workspace "/path/to/author code" --service "/path/to/laboratory"
scopecat app --workspace "/path/to/author code" --home "/path/to/application home"
```

Registration writes only the local runtime binding and source membership. It does
not copy laboratory declarations into author code or install packages. The source
cannot be registered as another application service. In a Notebook opened in that
folder, `session = sc.notebook()` uses the same bound laboratory.

This form requires the laboratory's `[lab]` table to contain only `adapter`:
maintained capabilities belong in the installed adapter. Projects with local
bootstrap/driver trees continue to use their combined project declaration. Author
sources can have different source boundaries and dependencies, qualified against
the same interpreter; they cannot select another adapter or execution environment.

Each source revision captures the selected adapter declaration as
`scopecat.laboratory.toml` alongside the original author manifest and exact installed
package identity. This file is internal snapshot content, not a file authors must
maintain. Execution and validation load that captured declaration. Missing pins or
changed installed artifacts fail rather than following the current laboratory.
Current-format backup retains this evidence; restore still requires explicit local
source registration before executing its code. Adapter artifacts must remain
available separately: the snapshot does not archive installed wheels.

## Open a Notebook in the registered environment

From the already registered author folder, run:

```shell
scopecat notebook --home "/path/to/application home"
```

Or supply the author folder explicitly: `scopecat notebook "/path/to/author code"
--home "/path/to/application home"`. Add `--no-browser` when you want to open the
printed Jupyter URL yourself. Run this in a local terminal; it remains attached to
the Notebook server. The selected laboratory delivery must contain
`scopecat-lab-tools[notebook]` (the public builder's `--notebook` option or an
appropriate laboratory recipe dependency). Missing extras produce an error; the
launcher does not install packages or choose another Python.

This resolves the source's registered laboratory and launches JupyterLab and its
**Scopecat 实验环境** kernel with that laboratory's interpreter. Each launch uses
its own temporary kernel configuration; it does not install a global kernelspec or
overwrite another running Notebook server's configuration. Existing notebooks that
request another kernel must select **Scopecat 实验环境**. The working directory is
the author folder, and inherited Python-path/daemon-URL overrides are removed.

Opening this editor does not start the experiment service or initialize instruments.
Open the workbench separately when ready to work. Close this Jupyter server and its
kernels before updating the laboratory, then run the same command again: it resolves
the newly registered interpreter. An incomplete environment switch blocks launch.
Already running kernels and external editors such as VS Code are not redirected;
those editors still require explicit interpreter selection and kernel restart.
This entry does not supervise Notebook processes in the application manager.

## First use

A fresh installation opens **Set up the primary workbench (设置主要实验工作台)**.
Choose **Create** for an ordinary virtual experiment project, or **Connect** for
an existing laboratory code directory containing `scopecat.toml`. Choose **Installed
adapter (使用已安装适配包创建实验室)** for the separated laboratory/author setup
below. In the create/connect forms, the directory
is the primary editable code source, including its laboratory capability declarations;
it may select an installed laboratory adapter through `[lab.adapter]`. Use a trusted
directory and environment prepared by your laboratory.

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

Without a delivery selection, an existing project `.venv` is used; a broken one
is an error. Without it, setup uses the installed application environment.
Alternatively, select the laboratory's **offline delivery directory**: setup
verifies its files and Python/platform ABI, retains a copy in the application home,
and installs the locked packages offline into the project's final `.venv`. It uses
the matching GUI from that delivery. Python and uv must already be available;
this does not download Python or choose vendor SDK versions. Local laboratory
settings still come from the maintainer.

Errors remain in **Recent operations** and its log. A failed registration or startup
retains the created files; fix the reported environment and use **Connect** on the
same directory, or restart its registered service. Reopening the page does not replay
setup. To add another directory later, expand the setup form in management.

## Create an installed laboratory and bind author code

In first-use setup, choose **使用已安装适配包创建实验室**. Supply:

- A new **laboratory runtime directory**, separate from the author's code folder.
- A verified **delivery directory** or fixed build home containing the adapter.
- The adapter's distribution name and package-relative TOML resource path, supplied
  by the laboratory maintainer (for example `my-laboratory-adapter` and
  `my_lab/adapter.toml`).
- Optionally, an existing **author code directory** containing a source-only
  `[authors]` manifest, and the local settings file / new scientific-data directory.

The application generates the minimal `[lab.adapter]` laboratory manifest,
installs the delivery environment, registers the laboratory, and registers the
selected author directory through that same interpreter. It does not copy author
code, drivers or application factories. Registration verifies source composition
and dependencies before startup. The final **Create and open** action starts the
laboratory (which may initialize instruments) and opens the workbench with the
registered author's catalog selected; it never submits a measurement.

The author directory must already exist. The separate basic-project **Create**
option still generates its own example code; it cannot bind a source-only folder
to that local-driver example. Source-only folders require the installed-adapter
laboratory described above.

If environment preparation or source registration fails, files and any completed
registration are retained, and setup has not started the service. Correct the
reported cause, choose **Connect**, select the same laboratory and author directories,
and retry with the same delivery. Existing service/source IDs are preserved. Do not
repeat **Installed adapter** with a different runtime directory to work around the
failure. Connect can also add an author folder to a stopped installed laboratory;
leave the author field blank when no new source registration is needed.

After setup, `scopecat app --workspace AUTHOR --home HOME` and
`scopecat notebook AUTHOR --home HOME` use that binding. Notebook extras must be
included in the delivery. This removes manual laboratory-manifest authoring and a
separate register-workspace command for this setup path; building the adapter and
preparing its author examples still belong to laboratory maintenance.

## Installed laboratory adapters

A laboratory may provide an installed wheel plus a small editable experiment
directory. The directory selects the adapter and its own experiment modules; it
does not need copied drivers, compiler code or an application factory. Select the
maintainer's complete offline delivery during setup to prepare `.venv`,
or use an already prepared environment. Setup checks the selected interpreter
without importing drivers into the manager. It does not search the internet for
missing packages.

Local experiment edits use normal notebook refresh. To change the adapter, use
the stopped delivery update below. A maintainer who intentionally changes packages
in place must stop first and recheck the existing environment afterward. The registered content fingerprint detects even same-version file changes.
Keep exact old wheels when retaining historical execution environments: scientific
source capture records installed package identity but does not archive the wheel.
If an adapter is missing or damaged, status and stop remain available; restore the
package before rechecking or starting.

## Build a laboratory delivery

Maintainers build on the recipient platform and Python ABI from their locked
build environment. For repeated development builds, keep one output home outside
the source checkout. In PowerShell, for example:

```powershell
python -m lab_tools.delivery --recipe "D:\LabSource\delivery.toml" --output-home "D:\Scopecat-Builds"
```

Replace the two paths with your maintained recipe and build directory. Run the same
command after code changes; do not create numbered output folders. Each attempt is
retained under `builds/`, including failures. Only a completed, integrity-checked
build atomically updates `delivery-current.json`. A build already using that home
blocks a concurrent attempt. Failed builds leave the previous selection unchanged.

Use that same output-home path in the application's initial setup or **Update
laboratory environment** field. Installation resolves the selected artifact once
and retains its identity; a later build does not switch an installed runtime.
The pointer is not evidence of successful installation or hardware acceptance.
If no build has succeeded yet, there is no installable current delivery.

For a one-off artifact, `python -m lab_tools.delivery OUTPUT --recipe RECIPE.toml`
remains available; `OUTPUT` must not exist. Do not combine it with `--output-home`.
The recipe has one `[delivery]` table:

```toml
[delivery]
lock_project = "."
public_source = "scopecat"
dependency_group = "lab-delivery"
include_project = true
packages = [".", "packages/shared-methods", "scopecat/packages/scopecat",
  "scopecat/packages/scopecat-server", "scopecat/packages/scopecat-instruments",
  "scopecat/packages/lab-teaching", "scopecat/packages/lab-tools"]
```

Paths resolve inside the recipe directory. List all local wheel distributions,
including any additional framework extensions your adapter uses. The selected
lock project supplies dependencies and build constraints; `include_project = true`
includes its runtime dependencies as well as the named dependency group. The public
checkout supplies the installer, GUI and locked download toolchain. The builder
rejects duplicate distributions and records recipe identity in the delivery.

Development adapters are still installed from wheels; editable adapter installs
are not supported. Every invocation builds the declared packages and GUI (unless
`--gui` supplies a matching prebuilt GUI), using ordinary uv/pip/pnpm/build-backend
caches. There is no new source-based artifact-skipping cache. The manifest records
public/laboratory Git revisions (including dirty status), wheel/GUI hashes and tool
identity. Do not edit those checkouts during a build; retain the exact artifacts
when a dirty checkout is used.

The build needs network access and, unless `--gui DIRECTORY` supplies an already
built matching GUI, pnpm. Verify an offline installation and representative virtual
experiment before distributing the result. A built wheel inventory alone does not
prove the laboratory's runtime dependencies are complete.

## Retry an initial environment installation

Reconnect the same experiment directory and select the same delivery. A completed,
matching installation is reused and checked. A failed installation that belongs
to this installer is retained as failure evidence, then rebuilt at the original
`.venv` path. A still-running installation blocks retries. A working virtual
environment is never moved; another completed delivery or an unrelated `.venv`
is not overwritten. Scientific records and author files are preserved.

Once setup succeeds, the retained delivery supplies the GUI and receipt; ordinary
launch no longer needs the original copied delivery folder. Keep the original
artifact for maintenance and reinstall. This initial preparation is separate from
updating an existing deployment: use the stopped maintenance workflow below for
intentional environment updates.

## Local laboratory settings

The setup form can select a laboratory-provided JSON settings file. Leaving it
blank preserves the current selection; a new project without one uses adapter
defaults. Its path is stored in `scopecat.runtime.toml`, separately from scientific
data and deployment directories. Keep this file outside captured source roots and
version control. Registration checks JSON structure without importing laboratory
bootstrap or instrument drivers; the adapter validates its own typed fields when
loading its bootstrap. Registration alone does not prove hardware readiness.

The registered settings identity includes the resolved path and exact file bytes.
After editing settings, stop the service and **Recheck environment**, then start
again. A changed or missing file prevents startup but never prevents stopping.
To select a different file, stop first and reconnect the same code directory with
the new file selected. Existing data and deployment locations are retained.

An adapter may use settings to choose the initial configuration recipe for a new
store. Rechecking or restarting does not replace an existing scientific
configuration. Review and apply scientific changes through the configuration
workflow; this is not a live simulator/physical mode switch. Settings selection
does not install an adapter, SDK or Python environment.

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

## Update a laboratory from a delivery

1. Finish measurements, close Notebook kernels and stop the laboratory service.
2. On its service card expand **Update laboratory environment (更新实验室环境)**.
   Enter the absolute path to the matching laboratory delivery and choose
   **Validate and update (验证并更新环境)**.
3. Wait for success in **Recent operations**. The application retains the delivery,
   prepares a separate environment in its home, and checks the laboratory and every
   registered author folder with that interpreter before switching registration.
4. Relaunch `scopecat notebook` from the author folder to use the updated environment.
   For another editor, read the interpreter path in **Project and environment**,
   select it and restart the kernel. Existing editor kernelspecs do not change
   automatically.
5. Explicitly start the workbench when ready. Preview new work before submitting it.

The laboratory ID, data/deployment paths, settings selection and author folder IDs
stay unchanged. Author interpreter bindings move together with the laboratory.
The operation log records the old/new registration and retained delivery path.
The service stays stopped: updating never starts instruments or resumes measurements.

Preparation or qualification failure leaves the previous registration intact. Correct
its cause and select the same delivery to retry; a completed candidate is reused.
If switching the source bindings and service registration is interrupted, ordinary
application startup is blocked. Repeat the **same delivery** update to complete that
switch; do not bypass it by starting through a lower-level CLI. If using a build
home whose current selection has advanced, select the original artifact directory
under its `builds/` folder instead.

Old environments, failed candidates and retained deliveries are not automatically
removed. Existing scientific records are not rewritten. This is an environment
replacement, not a scientific-data migration or a promise that historical plans run
with a changed adapter. Keep matching old artifacts/environments for archival use.

This action accepts an already built delivery or managed build home. Building from
public/private checkouts and selecting the new Notebook interpreter remain separate
steps; the complete development update workflow is tracked in #712. The registered
Notebook entry above handles JupyterLab selection; external editors remain explicit.

## Recheck after updating the existing environment

1. Finish measurements and close notebook connections. In the manager choose
   **Stop service (停止服务)** and wait for **Stopped (未启动)**.
2. Update the environment using the installation's tested package or delivery
   procedure. Initial setup does not upgrade a completed environment. Keep the same project,
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

For externally prepared environments outside the delivery workflow, a new
interpreter or GUI directory requires local registration with those explicit paths. Stop the old service first. Run the command in the manager's installed
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
