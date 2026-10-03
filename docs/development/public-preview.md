# Public previews and source development

Public publishes the desktop application and framework Python artifacts. A
laboratory supplies ordinary source or extension wheels, its dependency lock and
site settings; it does not rebuild the desktop. Consumers need no public checkout,
Git submodule, Node.js or pnpm. Application and author environments update separately.

## Native application

Run **Full acceptance → native-distribution** on the intended commit. Successful
Mac/Windows jobs publish Actions artifacts named `scopecat-preview-OS-COMMIT`.
Each contains the installer, `native-release.json` and `bundle.json`: source commit,
platform/architecture, runtime content identities, installer SHA-256 and the
dependency inventory. Download the artifact for the target platform; it is retained
for 14 days. Archive a qualified installer with its two manifests before expiry.
Do not treat the framework-only `preview-…` release below as an installer.

The native application includes its own Python and dependencies; ordinary startup
does not download or install them. Author dependencies and vendor SDK/firmware are
separate deliverables. An offline laboratory deployment must prepare and verify
those separately on its target platform. An empty PATH is evidence of independence
from system Python, not by itself evidence that all laboratory work is offline.

See [installation and maintenance](../how-to/maintain-application.md),
[Mac first open](../how-to/mac-preview.md), and the
[packaging qualification](architecture/desktop-packaging.md). These are unsigned
Windows/ad-hoc-signed Mac previews, not notarized production releases.

## Publish and consume a preview

After CI and self-review, merge the public change and run
**Full acceptance → public-preview** on that commit. This uses the same registered
workflow as native qualification; the separate undiscoverable preview workflow
has retired. The job requires successful CI for the exact commit and builds
from `git archive`, stamps each wheel with a unique development version, and
publishes a GitHub prerelease named `preview-<full commit SHA>`. `preview.json`
records wheel versions, the commit and artifact SHA-256 hashes. Published assets
must never be replaced; publish a new commit to correct an artifact.

A consumer's `tool.uv.sources` selects the release's wheel URLs; its committed
`uv.lock` fixes those artifacts and third-party dependencies. A separate JSON pin
contains the HTTPS `url` and `sha256` of `preview.json`. Keep both pins in the same
consumer commit and verify wheel hashes against the preview before accepting it.

For local artifact inspection, use the same build entry. It reads the commit archive,
not uncommitted worktree changes; publishing remains the workflow's explicit action:

```sh
uv run --locked python scripts/build_preview.py build/preview --ref FULL_COMMIT_SHA
```

`build/preview` must be absent before building. Keep only the current candidate and
useful failure reports; publish before removing it. The low-level recipe builder
still supports explicit offline environment/teaching bundles. Those are build
inputs or specialist deliveries, not the normal laboratory desktop installation.

## Run without installation

From the public root:

Install Python 3.14+, uv, Node.js and pnpm first. The launcher installs the locked
frontend dependencies on explicit development startup.

```sh
uv run --group delivery python -m lab_tools.dev --source .
```

This starts a backend and Vite in the foreground, prints URLs and never opens a
browser. Ctrl-C stops both. Data stays in `.scopecat-dev`; `--home` selects another
development directory. It does not create a release, desktop entry or installation
selection. Use `--workspace` to register laboratory author source. Registration
does not select or activate its driver factory. Use **Update from source** in
**Devices and drivers** when you want to use that implementation; an unavailable
vendor environment does not prevent starting the development application.
Device connection remains explicit. Source registration bindings created by the launcher are
removed after successful shutdown; an existing binding is retained.

Consumers can run against the pinned preview GUI with `--preview`, or explicitly
overlay a public checkout's editable packages for framework work. Overlay all
public packages together and preserve the consumer lock. A checkout may live
anywhere; a sibling directory has no special meaning. Framework changes require
restart; author source refresh and idle driver source updates use the normal APIs.
