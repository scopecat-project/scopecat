# Configuration ownership convergence

Tracking: [#754](https://github.com/scopecat-project/scopecat/issues/754).

The current implementation has independent setup selection and parameter working
points, but `ConfigProfileSnapshot` still carries both through storage and planning.
Do not treat this combined representation as the final management API.

## Intended owners

| Owner | Responsibility |
| --- | --- |
| Local connection settings | SDK locations, network addresses and deployment secrets |
| Executable setup | Device capabilities, physical routing, lifecycle and resource authority |
| Measurement binding | Selected target(s) and their mapping onto available resources |
| Parameter workspace | Declarations, values, provenance and working-point revisions |
| Author revision | Experiment/analysis code and selected recipes |
| Resolved run inputs | Exact selected revisions and request-local overrides retained for execution |

The final run snapshot may aggregate these inputs. Editing a parameter workspace
must not change device authority. Selecting an author directory must not create a
new physical access domain. Simulation must declare its model and coverage; it
must not silently substitute for a physical connection.

Topology and `primary_entity_id` still live in executable setup. Review their
division between installed resources and measurement binding before promising a
stable model. Similarly, one daemon-wide active setup is the current authority,
not a decision that all future independent targets must switch together. Do not
invent a comprehensive physical asset model before concrete consumers require it.

## First slice: explicit composition and setup-first initialization

Adapters can construct `ExecutableSetupSnapshot` without owning parameter tables.
`compose_configuration(setup, id=..., system_id=..., catalog=..., parameters=...)`
in `scopecat.config.resolution` validates a combination without saving or selecting
anything. Its full-config result is the existing execution/registry carrier.

`lab.setup.save(setup, name=...)` and `lab.setup.activate(revision)` work in an empty
catalog. Parameter publication can follow without changing the selected setup.
The new test starts without a full bootstrap fixture and verifies both owners
across reopen. This provides an independent path; it does not remove the old one.

## Remaining changes, in order

The schema 84 registry now stores `ParameterRevisionContent` separately from
content-addressed executable setup payloads. Entries retain an exact setup content
hash; identical setup content is shared without activating or creating a named
setup revision. Reads reconstruct the existing full execution snapshot and verify
the retained setup hash. Metadata differences are preserved, even when execution
semantics are unchanged. Current-format backup/restore includes both parts.

The HTTP publication source `parameter_revision` and Python
`lab.config.set_parameter_default(...)` now accept only parameter content and an
exact saved setup reference. Publication resolves that revision on the server,
retains its identity as provenance and requires a compatible selected setup.
It never initializes or selects setup implicitly. The repository port still
accepts and returns full configurations, and run evidence
still retains complete execution snapshots. Do not infer new calibration validity
or compose historical parameters with the currently active setup on read.

1. Move remaining maintained consumers and fixtures to parameter-only inputs and
   explicit first-use orchestration, then remove the special
   case that initializes setup during the first full-config publication.
2. Separate measurement-target binding from device setup where required by real
   consumers. Define compatibility and independent execution by resource overlap,
   not by author-folder boundaries or a global parameter default.
3. Centralize resolution of exact setup/binding/parameter/author revisions into
   execution inputs, with explicit incompatibility diagnostics and retained source
   identities. A schema-compatible value is not automatically a valid calibration
   under a different setup or temperature.
4. Retire combined-config editing/bootstrap APIs after their maintained consumers
   use the new owners. Rewrite tests around independent creation, selection,
   parameter updates, conflicts and historical result reopening; preserve useful
   scientific assertions rather than every old fixture/interface.

Existing `bootstrap_config`, `set_default` first-publication behavior, full-config
working-point entries, setup rebinding and full-config experiment-system builders
remain transitional dependencies. Green tests for them do not close this issue.

No persistent scientific compatibility baseline is designated. Change current
APIs and current-format fixtures directly; do not add prebaseline readers or
migration edges. Retain historical files and owners' old environments, and keep
current-format backup/restore and scientific invariants covered.
