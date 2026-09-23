# Data formats before the compatibility baseline

Scopecat has **no designated persistent-data compatibility baseline yet**. Current
schema **95** is a development format identifier, not the start of a compatibility
promise. The target, setup, calibration and application ownership models must
settle before a supported baseline is explicitly designated with its formats,
retained fixtures, supported operations and upgrade policy.

The schema 68–74 copy-migration paths were development-machine exercises. They
have been retired, along with their CLI and migration guide. New Scopecat builds
do not promise to read, restore, replay or migrate any earlier prebaseline store,
snapshot, plan, source manifest or codec. A successful historical test does not
turn that format into a supported baseline.

## What remains supported now

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
