# Structured experiment authoring: long-term direction

Status: proposed architecture and staged evaluation plan, recorded 2026-09-13.
This is not a description of a shipped GUI editor, a storage migration contract,
or a commitment to implement every phase. Near-term request/API work should
preserve the boundaries below; representation and syntax remain subject to
prototype evidence. The existing supervised pilot and scientific acceptance
criteria remain in force. Track evaluation in [direction issue #502](https://github.com/scopecat-project/scopecat/issues/502).

## Outcome and scope

An ordinary laboratory author should be able to compose a complete experiment
from maintained components, inspect its meaning, change a quantum program or
supported instrument-control sequence, and share the definition without editing
compiler or driver code. A shared-capability author should be able to implement
new recipes and analysis in Python and expose a clear contract to that editor.
Complexity should be paid for when introducing scientific behavior, not when
repeating framework registration and connecting representations of the same field.

Complete composition does not require every component's implementation to be
graphically editable. A GUI-authored workflow may invoke a Python estimator,
a structured quantum program and a maintained device operation. Each component
has one authoritative definition and an explicit editing capability.

Initial non-goals are arbitrary Python-to-diagram round trips, a universal visual
Python language, editing compiled device instructions as author source, general
real-time multi-user collaboration, and making hardware-specific experiments
portable merely by exporting their pictures.

## Current foundations and missing contracts

The current implementation provides useful foundations:

- [Experiment dataflow](../../concepts/experiment-dataflow.md) describes value
  availability, typed outputs and measurement-dependent boundaries.
- Quantum authoring retains sequence, parallel, repeat, conditional, fragment
  calls, pulse recipes and entity-set operations in its internal fragment model.
  See `packages/scopecat-quantum/src/scopecat_quantum/authoring/_ir.py`.
- [Parameter declarations](../../how-to/declare-parameter-models.md) share editing
  fields and symbolic references, with a class-free exploration path.
- [Author revisions](../../how-to/refresh-author-code.md) preserve complete local
  source snapshots; prepared launches retain selected code and configuration.
- [Saved experiment plans](../../how-to/save-experiment-plans.md) and durable
  procedures already own run intent and multi-run execution responsibilities.

These are not yet a stable editable document format. Python construction can
specialize or expand authored loops and helpers; a resulting structure does not
necessarily retain the original editing intent. A program tree or lowered
schedule also does not automatically supply stable edit identities, component
migration, source maps, semantic merge or graphical capabilities.

The gap is an author-facing model between source construction and lowering,
with useful inspection projections. Adding more surface syntax or serializing
private fragment classes directly would not close that gap.

## Alternatives and chosen direction

| Alternative | Advantage | Cost / boundary | Direction |
|---|---|---|---|
| Python source with visual inspection | Immediate use of existing experiments and source history | General Python internals cannot be graphically rewritten | First delivery stage and permanent supported mode |
| Structured document with GUI and Python editing APIs | One editable source, predictable diff and validation | Needs a versioned author model and component contracts | Incrementally introduce for supported domains |
| Two independently editable Python and graphical definitions | Superficially seamless switching | Inverse reconstruction and conflict resolution are ambiguous | Do not promise arbitrary bidirectional synchronization |
| Restricted textual DSL round-tripping with a document | May provide a readable alternate editor | Syntax must preserve the same model; comments and source locations need treatment | Optional later frontend, not arbitrary Python |
| Canonical compiled IR as the document | Reuses an existing structure | Loses abstraction, couples authors to compiler versions and expansion | Keep compiled products as derived evidence |

Use Python-owned and document-owned components together. Uniqueness of authority
is per component revision, not a demand that an entire repository use one file
format. Distinguish definition identity, revision identity and each call-site
instance; editing a local call must not silently edit its shared definition.

## Model boundaries

| Layer | Owns | Does not own |
|---|---|---|
| Component definition | Input/output contract, supported author operations or Python source, dependencies | Live sample values, an implicit current machine |
| Experiment request / saved plan | Definition selection, concrete inputs, scan intent, execution options, permitted instance overrides | An independently edited copy of the definition |
| Prepared experiment | Frozen definition closure, parameter/sample context, resolved implementation selection and checked preview | Mutable editor state or automatic permission to retry acquisition |
| Lowered execution | Compiler/target-specific schedule, resources and generated programs | The authoritative author document |
| Run and analysis evidence | Actual execution facts, data, publications and acceptance decisions | A guarantee that the original environment still exists |
| Editor view state | Layout, folding, viewport and selections | Scientific or execution semantics |

Preserve calls, repetitions, entity selectors and parameter expressions at the
author level. Derived expansion and lowering may optimize these structures, but
must retain traceability to author nodes and call instances where available.
A node generated by opaque Python may have only a source-level location; the UI
must not invent an exact editable source mapping.

This is a logical model, not a decision to add six new public classes or stores.
Reuse existing request, plan, revision, preparation and evidence infrastructure.
Do not introduce a parallel execution engine for graphical experiments.

## Ownership, editing and conversion

A Python-owned component is edited through its source. The GUI can configure
exposed inputs, compose calls, inspect generated structure and navigate to source.
Its body is read-only in the graphical projection unless a specifically supported
source-edit adapter exists. Such an adapter must validate and update the original
source, preserve a reviewable diff and state its restricted syntax boundary.

A document-owned component is edited through a shared semantic edit API, whether
called by the GUI or Python tooling. A generated Python rendering is a projection,
not a second authority. Changes to generated files are not silently imported.
Exporting standalone editable Python is an explicit conversion to a new artifact.

Likewise, freezing a Python-generated structure into a document creates an
independent component. Record the source revision and arguments used. Explain
what was specialized or expanded and what can no longer be adjusted through the
original generator. Parameter dependencies must remain explicit unless the user
chooses concrete substitution; freezing must not accidentally disclose or embed
all current laboratory parameters.

Instance overrides need a declared interface. Overriding a declared pulse width
at one call site is different from patching an arbitrary nested node. For an
internal change, edit the shared definition or explicitly fork it. No hidden
priority chain between generated code, GUI patches and current Python files.

## Minimal author model and extension contract

Start with operations required by an evaluated workflow, not a universal AST:

- Typed literals, inputs, parameter references and a bounded expression vocabulary
  for arithmetic and unit conversion. Arbitrary evaluation of expression strings
  is not an interoperability mechanism.
- Named component calls with typed ports, stable call-site IDs and outputs.
- Explicit sequencing, parallel composition, bounded repetition and supported
  conditional forms, each with defined execution placement.
- Quantum operations, measurements, pulse recipe calls and entity/edge selectors.
- Supported device operations with declared arguments, outputs and resource/effect
  information sufficient for planning. Free-form SDK commands are not ordinary
  author nodes.

A component contract supplies identity/revision, input/output schema, defaults,
units, allowed bindings/scans, parameter dependencies, declared capabilities,
validation, and inspection information. Effects or resources that cannot be
known statically must be resolved before admission or reported as unknown; do
not present inferred completeness for arbitrary Python.

Use generic forms and generic node inspection as the baseline. Specialized
circuit, pulse and instrument editors are optional views of the same contract.
Do not require every component maintainer to ship frontend code just to expose
one operation. An unknown component must remain visibly unresolved and preserved
on save; never drop its fields or allow an unsupported execution to proceed.

Schema validity, program-build validity, target compilability and readiness for
execution are different results. An incomplete document may be saved for editing
without being runnable. Missing parameters can be reported for the operations
that actually consume them. Draft validity is not scientific calibration evidence.

## Execution semantics must remain visible

Device operations have ordering, completion and ownership semantics. Setting a
bias, waiting for declared stabilization, checking readback and acquiring data
must not be reduced to visually adjacent boxes with unspecified dependencies.
Resource conflicts and unsupported parallelism should be located at the relevant
steps. Editing or rendering a document must never perform device I/O.

Keep these distinct:

- Python construction-time branching specializes a program.
- A supported bounded real-time branch runs inside a target with known execution
  guarantees and resource bounds.
- Host analysis feedback ends a run and controls subsequent runs through the
  existing procedure model.
- Adaptive point generation uses the existing explicit adaptive-domain boundary.

The same diamond or loop icon must not conceal these differences. Readable labels
and capability checks must state where a branch executes. Generic GUI nodes must
not weaken leases, timeouts, admission checks, cancellation or evidence rules.

## Views that remain usable as scale grows

Use multiple coordinated views instead of a single universal node canvas:

| Task | Primary view | Inspect on demand |
|---|---|---|
| Coarse-to-fine calibration | Step outline and checkpoints | Run requests, analysis, candidate lineage |
| Instrument preparation | Ordered steps and dependencies | Readback, completion, resource conflicts |
| Quantum composition | Hierarchical circuit or sequence | Selected targets, subprograms, implementation choice |
| Pulse recipe editing | Local timeline and field/expression editor | Expanded envelope and sample preview |
| Large target sets | Topology/table selection and group operations | A selected qubit, edge, exceptional override |
| Execution diagnosis | Compiled schedule/resource view | Source node, compiler revision, actual run events |

Preserve 'apply this recipe to this target set' rather than materializing thousands
of author nodes. Shared definitions, call instances and per-target exceptions
must be visually distinct. Searching, keyboard edits, bulk changes, filtering and
structured tables are first-class operations; dragging each gate is not the
scale strategy. Expansion must be lazy or bounded, with an explicit limit and
summary for very large programs. Layout positions must not define execution order;
order and synchronization belong in the model.

Author intent, expanded structure, scheduled operations and actual device output
are separate views. A pulse-shaped illustration is not a claim about emitted
hardware samples. Large-scale view tests need both compact generative examples
and irregular experiments with many exceptions, not only repeated identical gates.

## Revision, storage and merge

Use stable author IDs and deterministic, reviewable text serialization. The exact
format is undecided; raw dumps of private implementation classes are not a public
format. Avoid gratuitous IDs or ordering changes on save. Definition revisions
capture a dependency closure, so opening an old revision does not resolve shared
recipes to today's latest implementation.

Keep semantic content identity separate from presentation layout. Moving a box
must not alter the request's scientific meaning or invalidate an otherwise
identical prepared program. Integration with current whole-source manifests needs
an explicit design: simply storing layout beside source would still change a
whole-bundle hash. Separate classification/digests must be implemented and tested
before claiming layout-neutral revision behavior.

Provide semantic diff before automatic semantic merge. Show changes such as
'half-turn implementation replaced', 'delay expression changed', or 'target set
expanded'. Use base revisions for edits; preserve concurrent work through explicit
conflicts. Start with ordinary Git review and optimistic editor saves. Schema,
ports and component-version changes deserve conflict review even when textual
merging succeeds. Do not require a CRDT to ship the first editor.

Distinguish editor undo, reverting a saved definition, reverting laboratory
configuration and cancelling execution. They have different consequences. Editing
an old version creates a new revision; it never changes a retained run.

Document migrations are explicit and create a new artifact/revision. Historical
records retain the original representation and its declared format version.
Long-term read-only inspection may need archived normalized views or readers;
do not promise unlimited historical editability. Prototype this before choosing
a permanent format. A migration must not silently update scientific recipes.

## History, replay and sharing

Archive enough to distinguish the authored definition, the prepared binding and
the realized execution. Retain component revisions, parameter/sample context,
implementation selection and relevant compiler/environment identities, with
actual generated artifacts where already supported or explicitly added.

Historical inspection must not execute arbitrary archived Python just to render
a graph. Prefer retained structural projections. Re-execution is a separate action
that checks environment and device compatibility. Current author revisions record
environment versions but do not archive a reproducible installation; this proposal
does not retroactively turn them into one.

Define separate export profiles: reusable definition, reviewed plan, and an
optional evidence package. The reusable definition includes component dependencies,
parameter contracts and required capabilities, with logical target bindings rather
than machine addresses. Actual calibration values, raw data, device endpoints and
credentials are separate choices, not incidental additions to a diagram export.

An import should report missing components, incompatible versions, unresolved
logical targets, parameter contracts and environment requirements. Completing a
binding creates a local revision; it must not rewrite the imported original.
Inspection of an untrusted package should be possible without executing its Python.
Executable dependencies follow the existing trusted-code/environment boundary.
Successful import or compilation does not establish physical equivalence or
scientific validity on another laboratory setup.

## Constraints on the near-term request design

Function-style creation remains a strong candidate: calling an experiment creates
a request rather than acquiring data. Whether that request is mutable, uses an
explicit input model, or has a concrete request type is still under evaluation.
The first managed implementation offers `experiment.request(...)` with mutable
`values`, explicit `Scan` intent, isolated copies and source-contract matching.
It reuses preparation and saved plans; ordinary calls still build invocations.
`Annotated[Input[T], ControlSpec(...)]` now derives numeric controls and symbolic
references from the signature, including required controls without fake defaults.
See [the notebook workflow](../../how-to/managed-author-session.md#edit-a-request-before-preparing)
for its delivered typing and version-selection boundaries.
Do not choose a public syntax solely to resemble future GUI internals.

Regardless of syntax:

1. A request references one definition contract and an explicit version-selection
   policy. Resolve any current-version selection during preparation; retained
   prepared requests pin the result.
2. Bind inputs, scans, options and permitted overrides through one semantic path
   shared by Python and GUI. Arrays are not implicitly scans; fixed/single-point
   scans and grid/paired traversal remain distinct.
3. Editing request structure rebuilds from its authoritative definition. Do not
   attach ad hoc patches to compiled IR or capture a hidden second definition.
4. Preparation freezes inputs, including mutable arrays, source/document closure,
   selected parameters and implementation choices. Early request validation may
   report errors before costly compilation.
5. Requests cannot silently execute a stale imported Python definition when the
   session has selected another revision. A declared mismatch needs resolution.
6. Saving/reopening a request reuses the existing plan and evidence infrastructure;
   graphical plans must not acquire a separate submission/recovery mechanism.

A GUI edit-request representation and public Python request type need not be the
same class. They must express the same semantics and validation outcomes.

## Staged roadmap and exit criteria

| Phase | Deliverable | Evidence required before expansion |
|---|---|---|
| S0: contract and fixtures | Component ownership, definition/request boundary and representative journeys | Review model against existing Python examples; identify opaque regions honestly |
| S1: read-only inspector | Inputs → parameters → selected recipes → expanded structure, linked to retained runs | Explain Rabi/Ramsey dependency failures and old/new implementation differences without raw records; no device calls |
| S2: restricted document prototype | Stable nodes, calls, edits, serialization and one structured program | GUI and Python edits yield the same normalized semantics; layout changes do not change execution identity |
| S3: quantum editor | Sequence/parallel/repeat, maintained gates and local pulse recipes | Round-trip supported operations, keep large target sets compact, diagnose unsupported target capabilities |
| S4: device and workflow composition | Declared device steps plus existing multi-run checkpoints | Ordering/resource behavior and recovery match current execution; host feedback is not confused with real-time control |
| S5: history and exchange | Dependency-pinned exports, semantic diff, explicit upgrades and binding | Reopen old definitions after component changes; inspect unsupported packages without executing them; import into a different logical setup |

S1 can proceed with existing Python components before selecting a canonical
document format. S2 is required before a production writable S3 editor. S4 and S5
can be explored earlier, but ownership, revisions and execution semantics must be
part of S2; they are not features to bolt on after a canvas is shipped.

Do not turn this table into a large implementation queue yet. Create bounded
implementation issues after each phase's unknowns and affected contracts are
resolved. Each slice should include producer, GUI/Python consumers and a real
workflow acceptance check; current pilot maintenance remains independently useful.

## Evaluation tasks, risks and open decisions

Use one scenario corpus across API prototypes and GUI views:

- Compose Rabi, leave calibrated gate amplitudes unknown, and explain why Ramsey
  cannot consume its half-turn parameter yet.
- Modify a Ramsey subprogram, preview affected timing, and compare old/new runs.
- Apply one recipe to 2, 32 and 256 logical targets; introduce a few exceptions and
  inspect one target without expanding the whole program. These are proposed
  synthetic workload sizes, not validated hardware capacity claims.
- Compose bias setting, stabilization/readback and acquisition using a fake device;
  detect a resource conflict before running and interrupt the resulting workflow.
- Change a shared recipe while another author edits a call; inspect the conflict
  and retain the original plan's dependency revision.
- Share a definition with a missing Python component or a different device mapping;
  inspect requirements without executing imported code.

Measure successful task completion, incorrect edits, help requests, ability to
explain what will run, and comparison/recovery accuracy with ordinary experiment
users. Also measure document size, expanded-node count, preparation time, UI
response and semantic diff size at increasing scale. Establish numeric budgets
from these fixtures before promising responsiveness. AI execution and synthetic
benchmarks complement, but do not replace, human or physical acceptance.

Open choices include format, expression vocabulary, component schema evolution,
source-map granularity, retention policy, presentation identity, request typing,
and whether a restricted textual frontend is worth maintaining. Prefer read-only
inspection if document editing does not materially improve the evaluated journeys.
Stop expanding generic graph features when users need more domain-specific
operations or better diagnostics instead. A useful mixed-source editor is a valid
outcome; universal graphical editability is not the success criterion.
