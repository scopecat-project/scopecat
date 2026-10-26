# Apparatus history and configuration ownership

Status: selected direction after the September 2026 user discussion. The first
implementation slice (#644) is descriptive apparatus history. Executable apparatus
targets, maintained setup composition and object-scoped calibration publication are
subsequent work, not capabilities established by this catalog.

## Start with useful history, not a digital twin

A physical line can be the object of a transmission measurement and part of the
apparatus in a chip experiment. Its identity survives that change of role. It must
not become a fake sample merely so its measurements can be found.

The immediate product test is whether a maintainer can find earlier measurements,
photos and connection notes more easily than by searching notebooks and slides.
It is not whether Scopecat can reconstruct every port, cable and connection. Keep
sample identity and descriptive apparatus identity explicit; do not introduce an
arbitrary object graph or require complete topology before recording useful data.

Three different claims must remain distinguishable:

| Layer | Claim | Authority |
|---|---|---|
| Object identity | These records concern the same named physical object | Catalog identity and the maintainer's declaration |
| Historical observation | This measurement or note was recorded under these stated conditions | Retained observation, attachments and linked run evidence |
| Current state or applicability | The apparatus is connected this way now, and this result is suitable for this experiment | A future explicit verification and applicability contract |

An uploaded wiring diagram establishes neither current wiring nor calibration
validity. A renamed object does not acquire a new physical identity. Two matching
labels in different catalogs do not establish that the objects are identical.
An object revision describes what was recorded about it; it does not certify that
the described hardware existed in that state at every point during the revision.

## Bounded descriptive catalog (#644)

Python/HTTP history operations own recording. Data now provides read-only
[graphical history browsing](../../how-to/record-apparatus-history.md#browse-retained-history-in-data)
using those existing operations. It does not replace notebooks or slides as the
everyday editing surface.

The first slice provides stable apparatus-object identities, exact immutable
description revisions and append-only observations. Observations reference an exact
object revision, retain optional declared conditions and observation time, own their
uploaded attachments, and can link retained runs. Historical observations therefore
do not silently acquire a later description when the object's head changes.

Recording time and declared observation time have different meanings: importing an
old room-temperature plot today must not imply that the measurement happened today.
Unknown temperature, connection arrangement or observation time stays unknown. Do
not infer cold conditions from a cooldown label, infer physical connections from
an execution route, or fill unknown conditions with convenient defaults.

Use a small descriptive surface. Names, aliases and notes can identify a line or
component without requiring port-level structure. Conditions record what the user
knows and are not executable predicates in this slice. Retain original documents
and plots instead of requiring users to transcribe all their contents into a
schema. Structured measurements can be linked when they exist.

A linked run remains an independent retained scientific record. The link says
that the user associated it with this observation; it does not rewrite its admitted
scientific binding, turn an unbound run into an apparatus-target run, or assert
that its configuration described the real wiring. Attachment ownership means the
history must not depend on a temporary upload path or a later-deleted teaching
sandbox. Current-format recovery must preserve the object revisions, observations,
attachments and links together.

The first useful maintenance journey is:

1. Register a recognizable apparatus object without creating a sample.
2. Record a room-temperature observation with an original plot or note and the
   conditions actually known.
3. Record a later observation, optionally linking an existing retained run.
4. Find both through the object history and inspect each original description,
   conditions and evidence independently.

This slice does not add apparatus objects to `ScientificSelection`, resolve their
execution topology, provide a live connection map, invalidate chip calibrations,
or automatically use an observation as a parameter source. Search, display and
comparison convenience can grow from this history without making those claims.

## Reference, adoption and calibration are separate

Room-temperature and low-temperature results may differ substantially. Neither
sharing an object identity nor being the newest observation proves interchangeability.
The following are prospective distinct operations, not automatic promotions:

| Use | Required meaning |
|---|---|
| Reference | A person can inspect the original result and its declared conditions; no automatic suitability assertion |
| Explicit parameter adoption | A particular experiment or parameter revision records the selected source, any transformation and the resulting values |
| Qualified calibration | A publication records evidence and explicit applicability conditions that the calibration resolver can check |

An observation can remain reference material indefinitely. If room-temperature
data is used as an estimate for cold operation, retain that decision and any
conversion; do not relabel the source as a cold measurement or fresh calibration.
Missing conditions may be acceptable for reference material while making automated
reuse ineligible. The appropriate conditions depend on the scientific operation;
do not invent a universal temperature or age threshold.

Adoption and dependency design should follow an actual maintained workflow. The
catalog itself neither supplies automatic calibration freshness nor demands that
users keep it synchronized with every physical intervention.

## Executable configuration and remaining scientific boundaries

The [configuration ownership contract](configuration-ownership.md) owns current
parameter branches, setup definitions/resolutions and execution fences. The former
global-default selector and combined working-point writers are retired. Descriptive
apparatus history neither selects execution inputs nor supplies calibration acceptance.

Executable setup declarations contain what software needs to execute; additional
verification is needed to claim that real wiring matches them. Association with
catalog objects must be explicit. Port-complete apparatus documentation is not a
prerequisite for an otherwise valid setup.

Independent parameter branches have separate publication conflicts. This does not
permit concurrent access to one physical instrument: admission still enforces
resource authority and stale-device protection. Each run retains its complete
resolved configuration and provenance, rather than reconstructing it from mutable heads.

The following scenarios distinguish existing independent publication from remaining
executable-apparatus and cross-object work. Descriptive observations alone do not
satisfy the latter boundaries:

| Scenario | Required observation |
|---|---|
| A and B calibrate independently | Separate state, dependencies and publication revisions; A's update does not stale B merely through one shared scientific version; shared instrument access remains serialized or otherwise safely arbitrated |
| Measure a physical line without a sample | Descriptive observations work now; later executable measurement retains the exact apparatus subject and conditions without inventing a sample or retroactively relabeling an old run |
| Chip calibration depends on line calibration | An explicitly qualified line publication is a retained dependency; relevant condition/setup changes affect only declared dependents; room-temperature reference material never automatically satisfies a cold-operation requirement |

These scenarios, rather than a more general entity hierarchy, determine whether
additional fields and relationships are necessary. No persistent-data baseline is
established by this work. Follow the [data compatibility policy](../data-compatibility.md):
keep historical files intact, maintain current-format backup/restore, and do not
add prebaseline readers, migration edges or guessed historical apparatus bindings.
