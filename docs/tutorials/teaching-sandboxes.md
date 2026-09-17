# Runnable tutorial sandboxes

Start from one complete Notebook, make a small change, then discard or reset the
exercise. No reference-lab checkout, laboratory configuration or AI assistant is
required. These tutorials use synthetic computation and never connect devices.

## Install once

Obtain the tutorial delivery for your operating system, CPU and Python 3.14 ABI.
Prepare Python 3.14, uv and VS Code with its Python/Jupyter extensions. From the
received delivery directory run `python install.py`. The installer verifies and
copies the complete offline delivery into `~/Scopecat-Lab`.

On Windows, open `Scopecat-Lab/lab.cmd`; elsewhere run
`python ~/Scopecat-Lab/lab.py`. Choose a topic, then open, reset, stop or verify.
The installed command `scopecat teach` exposes the same menu. There is no need to
activate a different environment or number project folders by hand.

Each topic has its own environment and one complete Notebook:

| Topic | Runnable starting point |
| --- | --- |
| `parameters` | Edit a typed parameter table and run a seven-point scan |
| `compute` | Return average complex IQ and read typed rows using a shared unit alias |
| `refresh` | Edit defaults, refresh author code and import a newly added experiment |
| `groups` | Analyze two groups from a retained scan and reopen their summaries |

Open the generated project folder and select its `.venv` kernel. The first cell
rejects the wrong interpreter. Run All before making one small change. The current
Notebook instructions are in Chinese; this page explains the common entry in English.
Detailed API explanations follow the [learning path](../getting-started/learning-path.md).

Repeated opening continues the same exercise. Before reset, close its old Notebook
kernel. Reset stops its service and creates a fresh copy without merging edits.
Old copies remain available until you explicitly remove them after closing their
kernels and services. Copy any source or Notebook you want to retain elsewhere.
Real scientific projects and retained experimental evidence do not belong in this
resettable directory and continue to use explicit backup and migration policies.

## Source development without GUI builds

From a Scopecat checkout, run `uv run python teach.py source`, or double-click
`teach.cmd` and choose 1 on Windows. The entry installs local wheels and locked
teaching dependencies, starts an API-only service and opens the selected topic.
It does not build the GUI or require Node. Only the exercise's author package is
editable; new framework/tutorial contents receive a new sandbox identity.

Stop old services and close kernels before selecting an updated source version.
Each exercise retains its original installed runtime. Source trials provide quick
API feedback; fixed deliveries provide reproducible installation acceptance.

## Maintainer delivery

`teach.cmd` choices 2 and 3 build and verify a fixed local delivery, respectively
with and without installing it on the build computer. The equivalent commands are
`uv run python teach.py release --install` and `uv run python teach.py release`.
A release requires a clean checkout, Python/uv, Node/pnpm and build-time network
access. Output directories are generated automatically. A failed stage prevents
subsequent stages from running.

The build exports the reviewed `uv.lock`, builds public wheels and a matching GUI,
and records hashes and source identity. Verification uses an empty cache and
runs the actual shipped notebooks outside the checkout, including source refresh,
new modules, history, recovery, duplicate installation and reset. Windows and Linux
run this in public installed-pilot CI. CI artifacts expire; retain accepted
release bundles separately. Successful CI is software evidence, not human or
physical-device acceptance.

An explicit standalone tutorial workspace can also be generated with
`scopecat init PATH --topic compute` in a teaching installation. The menu is the
default for disposable exercises; `init` without a topic still creates the small
virtual-instrument project used for integration and application development.

Laboratory-specific package selection, SDKs, addresses, bindings, acceptance
policies and scientific records remain owned by the consuming laboratory.
