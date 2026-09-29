# Public previews and source development

Consumers pin published wheels and the workbench from one public commit. They do
not need a public checkout, Git submodule, Node.js or pnpm to build a laboratory
delivery. The public repository owns framework wheels, the GUI and delivery tools;
the consumer owns its dependency lock, laboratory code and delivery recipe.

## Publish and consume a preview

After CI and self-review, merge the public change and run **Public preview** on
that commit. The workflow requires successful CI for the exact commit. It builds
from `git archive`, stamps each wheel with a unique development version, and
publishes a GitHub prerelease named `preview-<full commit SHA>`. `preview.json`
records wheel versions, the commit and artifact SHA-256 hashes. Published assets
must never be replaced; publish a new commit to correct an artifact.

A consumer's `tool.uv.sources` selects the release's wheel URLs; its committed
`uv.lock` fixes those artifacts and third-party dependencies. A separate JSON pin
contains the HTTPS `url` and `sha256` of `preview.json`. Keep both pins in the same
consumer commit and verify wheel hashes against the preview before accepting it.

The laboratory delivery recipe builds only its own packages. Its locked
`delivery-build` group provides `pip`, `setuptools` and `wheel`. Run the installed
delivery tool with the recipe and manifest pin:

```sh
uv run --locked --group delivery python -m lab_tools.delivery \
  --recipe delivery.toml --preview public-preview.json --output-home builds
```

The tool verifies the GUI and public wheels belong to the selected preview, then
assembles the platform-specific offline delivery. Public previews are reusable
build inputs, not native application installers. Keep installation acceptance
separate from source development. Native packaging and platform directory defaults
remain separate work.

## Run without installation

From the public root:

```sh
uv run --group delivery python -m lab_tools.dev --source .
```

This starts a backend and Vite in the foreground, prints URLs and never opens a
browser. Ctrl-C stops both. Data stays in `.scopecat-dev`; `--home` selects another
development directory. It does not create a release, desktop entry or installation
selection. Use `--workspace` and `--composition` to select laboratory author source
and its `[lab]` declaration. Device connection remains explicit.

Consumers can run against the pinned preview GUI with `--preview`, or explicitly
overlay a public checkout's editable packages for framework work. Overlay all
public packages together and preserve the consumer lock. A checkout may live
anywhere; a sibling directory has no special meaning. Framework changes require
restart; author source refresh and idle driver source updates use the normal APIs.
