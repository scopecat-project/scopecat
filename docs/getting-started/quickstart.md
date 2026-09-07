# Pilot bundle quickstart

Install the locally supplied pilot bundle to create a project, retain one virtual
thermometer measurement, and inspect it in the project console. No laboratory
hardware, source checkout, Node.js, or GUI build is needed on the operator's
machine.

## Requirements and supported platforms

Use Python **3.14** and [uv](https://docs.astral.sh/uv/). The installed lifecycle is
verified in CI on Windows and Ubuntu Linux. Other Python versions and operating
systems are outside the pilot's tested envelope. This is a local, supervised,
single-lab installation; public package-index publication is not required.

Obtain the complete `scopecat-server` artifact directory from a pilot build. It
contains four Scopecat wheels, a GUI-bearing server source archive,
`requirements.txt`, and `manifest.json`. The manifest records Python and UI
versions, package versions, wheel/file SHA-256 hashes, source commit and dirty
state, and both dependency lock hashes. The requirements pin and hash all runtime
dependencies. Keep this directory with the project backups so the same reader can
be installed again. Third-party dependencies are downloaded from the configured
Python package index; this is not an offline wheelhouse.

## Install the bundle

From the artifact directory, run the same commands in PowerShell or a POSIX shell:

```sh
uv venv --python 3.14 .venv
uv pip sync --python .venv --require-hashes --only-binary :all: requirements.txt
```

The relative wheel paths in `requirements.txt` require this working directory.
Hash verification checks the artifacts against the supplied requirements; obtain
the bundle from your trusted pilot distributor.

Activate the environment in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Or in a POSIX shell:

```sh
source .venv/bin/activate
```

## Create and start a project

```sh
scopecat init ./my-lab
scopecat config check ./my-lab
scopecat start ./my-lab
scopecat status ./my-lab
scopecat open ./my-lab
```

`init` creates editable Python application and configuration source. `config
check` validates that source without creating project state. The initial
instrument is explicitly a virtual thermometer; it makes no hardware connection.

`start` serves the installed GUI, chooses an available loopback port, and records
it inside the project. Notebook clients and `open` discover this same endpoint.

## Measure and inspect

```sh
python ./my-lab/notebooks/01_first_run.py
```

The script prints a run ID, `completed` status, and a console link selecting that
run. Open the link, or select **First run** in the console. Under **Measurement
data**, open **Raw records** to inspect one temperature sample of **0.02 K**, its
resistance, and evidence identifying the virtual `thermometer` instrument.

The generated `configuration.py` declares the virtual instrument and its routing.
The `repetitions` parameter remains an editable starting parameter; the first
notebook intentionally takes just one sample.

## Restart and retain the result

```sh
scopecat stop ./my-lab
scopecat start ./my-lab
scopecat open ./my-lab
```

Select the same **First run** and confirm that its ID and measurement values are
unchanged. Restart can choose a different port, so reopen through `scopecat open`
instead of bookmarking the old daemon URL. Stop when finished:

```sh
scopecat stop ./my-lab
```

## Diagnose a failed first run

- **Missing application dependency:** a missing Python module or the project
  loader's `missing Python module` message identifies a dependency installation
  problem. Use this environment's Python, reinstall the matching bundle, and
  install any dependencies introduced by your edited application source.
- **Daemon unavailable:** `no daemon endpoint`, a refused HTTP connection, or a
  degraded `scopecat status` describes the service. Run `status`, then `start`;
  startup errors identify the project log. This is separate from an instrument
  connection failure.
- **Instrument connection failed:** instrument diagnostics identify the binding
  and driver. The unchanged starter uses only `kind="virtual"`; a device
  connection problem after editing configuration belongs to that binding.

## Build a bundle from source (maintainers)

From a clean checkout of the intended revision:

```sh
uv sync --locked
pnpm --dir apps/scopecat-ui install --frozen-lockfile
pnpm --dir apps/scopecat-ui run build
uv run python scripts/build_server_distribution.py
uv run python scripts/verify_pilot_bundle.py dist/scopecat-server
```

Distribute the complete `dist/scopecat-server` directory. Each build replaces that
artifact directory from a fresh staging area. The verifier installs into a fresh
environment outside the checkout, serves bundled assets, reads the generated
measurement, and checks it again after restart. CI also exercises installation on
Windows without a Node setup in the installation job.

Next, use the [reference lab tutorial](../tutorials/reference-lab.md) for further
virtual-instrument experiments, analysis and quantum calibration, or read the
[project layout reference](../reference/project-layout.md) before adapting the
application.
