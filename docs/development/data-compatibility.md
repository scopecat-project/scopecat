# Data formats before the compatibility baseline

Scopecat has **no designated persistent-data compatibility baseline yet**. Current
schema **112** is a development format identifier, not the start of a compatibility
promise. The target, setup, calibration and application ownership models must
settle before a supported baseline is explicitly designated with its formats,
retained fixtures, supported operations and upgrade policy.

The schema 68–74 copy-migration paths were development-machine exercises. They
have been retired, along with their CLI and migration guide. New Scopecat builds
do not promise to read, restore, replay or migrate any earlier prebaseline store,
snapshot, plan, source manifest or codec. A successful historical test does not
turn that format into a supported baseline.

## What remains supported now

Schema 112 adds application-owned parameter working drafts. Current-format backups
retain raw input, conflicts and completed/discarded history. Freezing input does
not save a parameter revision; checkpoint and draft completion are atomic. No
schema-111 reader or migration is added.

Schema 111 adds application-owned Decision draft revisions. Current-format backups
retain active edits, conflicting copies and discarded history. Drafts are not
scientific decisions and have no execution authority. No schema-110 reader or
migration is added; existing development data must be preserved with its pinned
reader, with a fresh data space for this build.

Schema 110 retains inert configuration exchange originals and atomic derivation
receipts alongside ordinary parameter branches and setups. Current-format
snapshots retain these tables. Imported source bytes are verified but never loaded
by inspection or derivation. No schema-109 reader or migration is added.

Schema 109 replaces calibration tasks' overwritten execution/check maps with
ordered check/repair/verification attempts. Budgets and the original start time
survive reopening and current-format backup. No schema-108 task reader or migration
is retained; historical files are not rewritten or removed.

Schema 108 adds bounded live-analysis follows and their append-only progress
events. Fixed measurement slices retain acquisition references without duplicating
arrays. Current-format export/import verifies those references, and cleanup waits
for active analysis. This change adds no prebaseline migration or fallback reader.

Schema 107 removes the global configuration activation and combined workspace-head
tables and activation-generation fields. Exact retained snapshot reads and current
parameter-branch recovery remain checked. No old-format reader or migration is added.

Schema 106 removes the unused global configuration operation receipt table.
Parameter branch receipts and retained scientific configuration evidence remain.
Opening an earlier development store rejects its version without rewriting it.

Schema 105 retains imported evidence in capture-owned object directories and
records each capture's membership of source-qualified run identities. Ordinary
cleanup can remove one capture without releasing identities retained by others;
its deletion fence remains until file cleanup succeeds. Importing does not restore
device registrations or runnable tasks. Earlier development stores are not
migrated or rewritten when opened.

Schema 103 retains driver source selections together with their immutable source
bundles. Activation commits with device connection heads. Current-format restart
and backup/restore load the selected bytes, not the original development directory.

Schema 102 adds shared data-cleanup receipts, deletion fences and practice ownership.
Run and analysis bytes are stored under their record owner, so explicit cleanup can
reclaim them without deleting another record's content. Current-format snapshots
retain these namespaces and editable practice files. No earlier object-layout
reader or conversion is provided.

Schema 101 retains application device registrations, immutable connections and
driver identities, setup definitions and exact resolutions, independent parameter
branches, and each run's explicit execution setup. First-use seed inputs commit
atomically without global setup activation. Current-format backup retains these
records together; earlier development stores are neither migrated nor rewritten.

Schema 99 requires explicit registered source identities for authored requests and
retained author records. An application store no longer seeds an implicit service
source. Earlier stores remain untouched; no source-identity rewrite is provided.

Schema 98 retains optional task finalization calls, their admitted procedure
association and admission failures. Adopted stage evidence is frozen in the
procedure intent within the association transaction. No prebaseline conversion
or migration is provided; existing historical stores are left untouched.

Schema 97 retains explicit stage candidate-output bindings and resolved checks
alongside task execution associations. These records commit together and replay
without consulting a newer analysis. No conversion of earlier task records is provided.

Schema 96 lets retained check/task contexts identify exact candidate inputs as well
as saved parameter revisions. Its context identity and indexed evidence are a new
development format. No prebaseline context conversion or index backfill is provided.

Schema 95 adds explicit parallel/sequential candidate composition provenance.
Sequential sources retain their order and exact predecessor consumption; their
net changes are revalidated on publication. Use a fresh development store rather
than rewriting schema 94 data. Configuration export and setup codecs are unchanged.

- The current build operates on its current format and rejects other formats.
- [Stopped-project backup and restore](../how-to/backup-and-restore.md) retains and
  verifies the current format's database, immutable objects, source and identity.
- Within that format, immutable scientific evidence, exact references, optimistic
  updates, run addresses and resource-ownership checks remain real requirements.
- Refactoring code does not authorize deleting or silently rewriting existing
  historical files. Unsupported data is left in place; use a fresh data location
  for a fresh development format.

Current-format backup/restore is not a cross-version upgrade service. It does not
install environments, make archived Python executable in a new environment, or
permit independent writable clones to share one acquisition namespace.

The native desktop can offer an explicit fresh start after unsupported-format
rejection. It preserves the original home and selects a newly prepared empty
space; it does not relax format rejection or introduce any migration edge.
See [recovery instructions](../how-to/maintain-application.md#when-an-older-data-format-prevents-startup).

## Historical archives

Owners may keep original stores, snapshots, exported results, matching old source,
package artifacts and external dependencies for their own historical reading. That
is an archival arrangement maintained by the owner, not a supported reader or
migration path supplied by new Scopecat versions. Keep those originals separate
from new development state. Do not delete an old database merely to make a new
runtime start, and do not infer missing scientific identities from directory names
or today's selections.

## Development decisions

Update current producers, consumers and tests together when changing a prebaseline
contract. Do not add old codecs, missing-field fallbacks, legacy-plan adapters or
migration edges solely to preserve prebaseline development formats. Tests should verify the
current format, rejection without mutation, frozen scientific intent and actual
backup/restore. Long-term compatibility work begins only when the future baseline
is explicitly designated; the scope will be stated then rather than inferred from
all earlier development formats.
