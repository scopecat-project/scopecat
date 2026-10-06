import type { ScientificSelection } from "../launch/scientific-selection";
import { createPortal } from "react-dom";
import { ObjectParameterFields } from "./ObjectParameterFields";
import {
  mappedEntity,
  objectContextError,
  panelMatches,
  type ObjectParameterContext,
  type ObjectParameterPanel,
  type ParameterWorkspaceHandle,
} from "./object-parameters";
import { useImperativeHandle, useRef, useState, type Ref } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "../../api-schema";
import type { ParameterEntity } from "../../api-contract";
import { apiClient, apiData, ApiError } from "../../api-client";
import { errorMessage } from "../../lib/presentation";
import { ConfigurationExchange } from "./ConfigurationExchange";
import { SetupPanel } from "./SetupPanel";
import { ParameterValueField } from "./ParameterValueField";
import { getSetupDefinitions } from "./setup-api";
import { getParameterRevisions, type ParameterRevision } from "./parameter-api";

import {
  startParameterDraft,
  readParameterDraft,
  parameterDraftHistory,
  type ParameterDraftView,
  type ParameterDraftValue,
  type WorkingInput,
} from "./parameter-draft-api";
import { useParameterDraft } from "./use-parameter-draft";

const loadBase = (view: ParameterDraftView) =>
  apiData(
    apiClient.GET("/api/v1/parameters/revisions/{revision_id}", {
      params: { path: { revision_id: view.draft.base.revision_id } },
    }),
  );

type ParameterEditorHandle = {
  draftId: string;
  leave: () => Promise<boolean>;
  branchGeneration: () => number | null | undefined;
};

export function ConfigWorkspace({
  ref,
  objectPanel,
  daemonUnavailable,
  onSelectConfiguration,
}: {
  ref?: Ref<ParameterWorkspaceHandle>;
  objectPanel?: ObjectParameterPanel | null;
  daemonUnavailable: boolean;
  onSelectConfiguration?: (
    choice: components["schemas"]["ConfigurationChoice-Input"],
    workingInput?: WorkingInput,
    subject?: ScientificSelection["subject"],
  ) => void;
}) {
  const cache = useQueryClient();
  const [operator, setOperator] = useState("local-operator");
  const [selected, setSelected] = useState("");
  const [editing, setEditing] = useState<{ base: ParameterRevision; view: ParameterDraftView }>();
  const [objectContext, setObjectContext] = useState<ObjectParameterContext>();
  const openingObject = useRef(false);
  const editorSelection = useRef(0);
  const editor = useRef<ParameterEditorHandle>(null);
  const replaceEditor = async (next?: { base: ParameterRevision; view: ParameterDraftView }) => {
    const selection = ++editorSelection.current;
    if (editor.current && !(await editor.current.leave())) return false;
    if (selection !== editorSelection.current) return false;
    setEditing(next);
    setObjectContext(undefined);
    return true;
  };
  useImperativeHandle(ref, () => ({
    openObject: async (context) => {
      const error = objectContextError(context);
      if (error) throw new Error(error);
      if (openingObject.current) throw new Error("A working table is already opening.");
      openingObject.current = true;
      const selection = editorSelection.current;
      const owner = editor.current?.draftId;
      const requireSameEditor = () => {
        if (selection !== editorSelection.current || owner !== editor.current?.draftId)
          throw new Error(
            "The working editor changed while opening object parameters. Keep the current editor and try again from its context.",
          );
      };
      try {
        const head = context.resolution.branch!;
        const currentHead = await apiData(
          apiClient.GET("/api/v1/parameters/branches/{name}", {
            params: { path: { name: head.name } },
          }),
        );
        requireSameEditor();
        if (
          currentHead.generation !== head.generation ||
          currentHead.revision.revision_id !== head.revision.revision_id ||
          currentHead.revision.content_hash !== head.revision.content_hash
        )
          throw new Error(
            "The branch changed after this context was resolved. Resolve the context again; your working input is preserved.",
          );
        const matchesBase = (view: ParameterDraftView) =>
          view.draft.base.revision_id === head.revision.revision_id &&
          view.draft.base.content_hash === head.revision.content_hash;
        if (editing?.view.draft.working_branch === head.name) {
          if (!matchesBase(editing.view) || editor.current?.branchGeneration() !== head.generation)
            throw new Error(
              "The open working table has another baseline. Review it in Configuration before resolving an object context.",
            );
          setObjectContext(context);
          return;
        }
        if (editor.current && !(await editor.current.leave()))
          throw new Error(
            "Save the open working table before replacing it. Its input has been kept in Configuration.",
          );
        requireSameEditor();
        const view = await startParameterDraft({
          draft_id: crypto.randomUUID(),
          base: head.revision,
          actor: operator,
          working_branch: head.name,
          branch_generation: head.generation,
        });
        requireSameEditor();
        if (!matchesBase(view) || view.branch_changed)
          throw new Error(
            "The recovered working table has an older baseline. Open and review it in Configuration; its input has been preserved.",
          );
        const next = { base: await loadBase(view), view };
        requireSameEditor();
        if (!(await replaceEditor(next)))
          throw new Error(
            "Save the open working table before replacing it. Its input has been kept in Configuration.",
          );
        setObjectContext(context);
      } finally {
        openingObject.current = false;
      }
    },
  }));
  const [workingBranch, setWorkingBranch] = useState("");
  const branchHeads = useQuery({
    queryKey: ["parameter-branches", "work-table"],
    queryFn: () =>
      apiData(apiClient.GET("/api/v1/parameters/branches", { params: { query: { limit: 100 } } })),
    enabled: !daemonUnavailable,
  });
  const openWorking = useMutation({
    mutationFn: async () => {
      const head = await apiData(
        apiClient.GET("/api/v1/parameters/branches/{name}", {
          params: { path: { name: workingBranch } },
        }),
      );
      const view = await startParameterDraft({
        draft_id: crypto.randomUUID(),
        base: head.revision,
        actor: operator,
        working_branch: head.name,
        branch_generation: head.generation,
      });
      return { base: await loadBase(view), view };
    },
    onSuccess: async (next) => {
      await replaceEditor(next);
    },
  });
  const open = useMutation({
    mutationFn: async (base: ParameterRevision) => ({
      base,
      view: await startParameterDraft({
        working_branch: "",
        draft_id: crypto.randomUUID(),
        base: { revision_id: base.id, content_hash: base.content_hash },
        actor: operator,
      }),
    }),
    onSuccess: async (next) => {
      await replaceEditor(next);
    },
  });
  const versions = useQuery({
    queryKey: ["parameter-revisions"],
    queryFn: ({ signal }) => getParameterRevisions(signal),
    enabled: !daemonUnavailable,
  });
  const setups = useQuery({
    queryKey: ["setup-definitions"],
    queryFn: ({ signal }) => getSetupDefinitions(signal),
    enabled: !daemonUnavailable,
  });
  const current = versions.data?.items.find((item) => item.id === selected);
  const entities = [
    ...new Map(
      (setups.data?.items.flatMap((item) => item.definition.topology.entities ?? []) ?? []).map(
        (item) => [`${item.kind}:${item.id}`, item],
      ),
    ).values(),
  ];
  return (
    <section className="grid gap-4">
      {daemonUnavailable && (
        <p role="alert">
          Reconnect to save parameter edits. Keep this editor open until its draft is saved.
        </p>
      )}
      <header>
        <h2>Experiment configuration</h2>
        <p>
          Setups bind registered devices. Parameter versions hold scientific inputs; neither changes
          another page's selection.
        </p>
        <label>
          Operator
          <input value={operator} onChange={(event) => setOperator(event.target.value)} />
        </label>
      </header>
      <ConfigurationExchange
        operator={operator}
        onCreated={async (revision) => {
          await cache.invalidateQueries({ queryKey: ["parameter-revisions"] });
          await cache.invalidateQueries({ queryKey: ["parameter-branches"] });
          setSelected(revision);
        }}
      />
      <SetupPanel operator={operator} onSelectConfiguration={onSelectConfiguration} />
      <section
        className="grid gap-3 rounded-lg border border-line bg-panel p-4"
        aria-label="Parameter versions"
      >
        <h3>Working parameters and saved versions</h3>
        <label>
          Working parameter branch
          <input
            list="working-parameter-branches"
            value={workingBranch}
            onChange={(event) => setWorkingBranch(event.target.value)}
          />
        </label>
        <datalist id="working-parameter-branches">
          {branchHeads.data?.items.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
            </option>
          ))}
        </datalist>
        <button
          disabled={!workingBranch.trim() || openWorking.isPending}
          onClick={() => openWorking.mutate()}
        >
          Open working table
        </button>
        <p>
          Continue this branch's working inputs. Using them for an experiment is explicit; saving a
          version is optional.
        </p>
        {openWorking.error && <p role="alert">{errorMessage(openWorking.error)}</p>}
        <p>
          Start from an imported template or a saved version. Unknown values remain unknown; saving
          a parameter version does not validate a calibration. Drafts recover editing input
          separately.
        </p>
        <label>
          Saved parameter version
          <select
            value={selected}
            onChange={async (event) => {
              const next = event.target.value;
              if (await replaceEditor()) setSelected(next);
            }}
          >
            <option value="">Choose a version</option>
            {versions.data?.items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.id}
              </option>
            ))}
          </select>
        </label>
        {versions.error && <p role="alert">{errorMessage(versions.error)}</p>}
        {current && (
          <>
            <p>
              {current.actor}
              {current.note ? ` · ${current.note}` : ""}
            </p>
            <div className="flex gap-3">
              <button disabled={open.isPending} onClick={() => open.mutate(current)}>
                Edit a copy
              </button>
              <button
                onClick={() =>
                  onSelectConfiguration?.({
                    kind: "parameters",
                    ref: { revision_id: current.id, content_hash: current.content_hash },
                    overrides: [],
                  })
                }
              >
                Use for next experiment
              </button>
            </div>
          </>
        )}
        {open.error && <p role="alert">{errorMessage(open.error)}</p>}
        {current && !editing && (
          <ParameterDraftHistory
            base={current}
            onResume={(view) => void replaceEditor({ base: current, view })}
          />
        )}
        {editing && (
          <ParameterVersionEditor
            ref={editor}
            objectContext={objectContext}
            objectPanel={objectPanel}
            key={editing.view.draft.draft_id}
            base={editing.base}
            initial={editing.view}
            onSelectConfiguration={onSelectConfiguration}
            onFork={(view) => {
              if (editor.current?.draftId === editing.view.draft.draft_id)
                void replaceEditor({ base: editing.base, view });
            }}
            entities={entities}
            onCancel={() => void replaceEditor()}
            onSaved={async (saved) => {
              if (editor.current?.draftId === editing.view.draft.draft_id) {
                setSelected(saved.id);
                setEditing(undefined);
              }
              await cache.invalidateQueries({ queryKey: ["parameter-revisions"] });
              await cache.invalidateQueries({ queryKey: ["parameter-branches"] });
            }}
          />
        )}
      </section>
    </section>
  );
}

function ParameterVersionEditor({
  objectContext,
  objectPanel,
  ref,
  base,
  entities,
  initial,
  onFork,
  onSelectConfiguration,
  onCancel,
  onSaved,
}: {
  objectContext?: ObjectParameterContext;
  objectPanel?: ObjectParameterPanel | null;
  ref: Ref<ParameterEditorHandle>;
  base: ParameterRevision;
  entities: ParameterEntity[];
  initial: ParameterDraftView;
  onFork: (view: ParameterDraftView) => void;
  onSelectConfiguration?: (
    choice: components["schemas"]["ConfigurationChoice-Input"],
    workingInput?: WorkingInput,
    subject?: ScientificSelection["subject"],
  ) => void;
  onCancel: () => void;
  onSaved: (saved: ParameterRevision) => Promise<void>;
}) {
  const draft = useParameterDraft(initial);
  useImperativeHandle(
    ref,
    () => ({
      draftId: initial.draft.draft_id,
      leave: draft.leave,
      branchGeneration: () => draft.input.branch_generation,
    }),
    [initial.draft.draft_id, draft.leave, draft.input.branch_generation],
  );
  const { name, note, branch, actor: operator, values = [] } = draft.input;
  const [review, setReview] = useState<ParameterDraftView>();
  const [actionError, setActionError] = useState<string>();
  const patch = (update: Partial<typeof draft.input>) => draft.edit({ ...draft.input, ...update });
  const setName = (next: string) => patch({ name: next });
  const setNote = (next: string) => patch({ note: next });
  const branchRef = useRef(branch);
  const setBranch = (next: string) => {
    branchRef.current = next;
    setBranchReview(undefined);
    patch({ branch: next, branch_generation: null });
  };
  const heads = useQuery({
    queryKey: ["parameter-branches", "editing"],
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/parameters/branches", { params: { query: { limit: 100 } }, signal }),
      ),
  });
  const reviewedHead = useQuery({
    queryKey: ["parameter-branch-review", branch.trim()],
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/parameters/branches/{name}", {
          params: { path: { name: branch.trim() } },
          signal,
        }),
      ),
    enabled: !!branch.trim(),
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
  });
  const newBranch = reviewedHead.error instanceof ApiError && reviewedHead.error.status === 404;
  const head = reviewedHead.data;
  const reviewedGeneration = draft.input.branch_generation;
  const [branchReview, setBranchReview] = useState<{
    version: ParameterRevision;
    branch: string;
    generation: number;
  }>();
  const [completed, setCompleted] = useState<ParameterDraftView>();
  const [copying, setCopying] = useState(false);
  const closed = initial.draft.state !== "saved" || !!completed;
  const setValue = (id: string, value?: ParameterDraftValue) =>
    patch({
      values:
        value === undefined
          ? values.filter((item) => item.id !== id)
          : [...values.filter((item) => item.id !== id), value],
    });
  const openCompleted = async (view: ParameterDraftView) => {
    if (!view.draft.result) return;
    try {
      const saved = await apiData(
        apiClient.GET("/api/v1/parameters/revisions/{revision_id}", {
          params: { path: { revision_id: view.draft.result.revision_id } },
        }),
      );
      await onSaved(saved);
    } catch (failure) {
      setActionError(errorMessage(failure));
    }
  };
  const finish = async (discard: boolean) => {
    const result = await draft.finish(discard);
    if (!result) return;
    if (discard) onCancel();
    else {
      setCompleted(result);
      await openCompleted(result);
    }
  };
  const fork = async () => {
    if (copying) return;
    setCopying(true);
    try {
      if (!closed && !(await draft.flush())) return;
      onFork(
        await startParameterDraft({
          working_branch: "",
          draft_id: crypto.randomUUID(),
          base: initial.draft.base,
          actor: operator,
          copy_from: initial.draft.draft_id,
        }),
      );
    } catch (failure) {
      setActionError(errorMessage(failure));
    } finally {
      setCopying(false);
    }
  };
  const destination =
    objectContext && objectPanel && panelMatches(objectContext, objectPanel)
      ? objectPanel
      : undefined;
  const objectEntity =
    objectContext && destination ? mappedEntity(objectContext, destination.entityId) : undefined;
  const adoptionContext =
    destination && objectContext?.resolution.context.subject.kind === "registered_target"
      ? { subject: objectContext.resolution.context.subject, setup: objectContext.resolution.setup }
      : undefined;
  const content = (
    <section
      aria-label="Edit parameter version"
      className={
        destination
          ? "grid gap-3 rounded-lg border border-line bg-panel p-4 text-sm max-h-[760px] overflow-y-auto [scrollbar-width:thin]"
          : "grid gap-3 border-t border-line pt-3"
      }
    >
      {destination && objectContext && (
        <header className="rounded-lg border border-line bg-panel-soft p-3">
          <h2 className="font-semibold">Working parameters · {initial.draft.working_branch}</h2>
          <p className="text-sm">
            {objectContext.sample.content.display_name} · sample revision{" "}
            {objectContext.sample.revision}
          </p>
          <p className="text-xs text-text-dim break-all">
            Baseline {base.id} · setup{" "}
            {objectContext.setupName ?? objectContext.resolution.setup?.revision_id}
          </p>
          {adoptionContext && (
            <p>
              Experiment target: {adoptionContext.subject.ref.target_id} · revision{" "}
              {adoptionContext.subject.ref.revision}
            </p>
          )}
          <p className="text-sm">
            Current editable inputs. Selecting an object does not change the next experiment.
          </p>
        </header>
      )}
      <p role="status">
        {draft.status === "saved" || (draft.status === "failed" && !draft.hasUnsavedChanges)
          ? "Draft saved in application data"
          : draft.status === "saving"
            ? "Saving draft…"
            : draft.status === "conflict"
              ? "Your conflicting copy is retained. Review before continuing."
              : "Draft has unsaved changes"}
      </p>
      <p>Draft recovery does not save a parameter version, apply changes or run an experiment.</p>
      {draft.error && <p role="alert">{draft.error}</p>}
      {actionError && <p role="alert">{actionError}</p>}
      {draft.status === "failed" && draft.hasUnsavedChanges && (
        <button onClick={() => void draft.flush()}>Retry draft save</button>
      )}
      {draft.conflict && (
        <>
          <button onClick={async () => setReview(await readParameterDraft(initial.draft.draft_id))}>
            Review current draft
          </button>
          {review && (
            <>
              <pre>{JSON.stringify(review.draft.input, null, 2)}</pre>
              <p>Current draft: {review.draft.state}</p>
              {review.draft.state === "saved" && (
                <button onClick={() => void draft.resolve(review)}>
                  Keep my input after review
                </button>
              )}
            </>
          )}
        </>
      )}
      {closed && (
        <p>
          This draft is {completed?.draft.state ?? initial.draft.state}. Its inputs remain in
          history.
        </p>
      )}
      {completed && (
        <button onClick={() => void openCompleted(completed)}>
          Open saved parameter checkpoint
        </button>
      )}
      {closed && <button onClick={onCancel}>Close editor</button>}
      {closed && <button onClick={() => void fork()}>Edit another copy</button>}
      <fieldset disabled={draft.busy || copying || closed} className="contents">
        {destination && (
          <ObjectParameterFields
            definitions={base.catalog.definitions ?? []}
            values={values}
            entity={objectEntity}
            onChange={setValue}
          />
        )}
        <details open={destination ? undefined : true}>
          <summary>All parameters · advanced</summary>
          {(base.catalog.definitions ?? []).map((definition) => {
            const value = values.find((item) => item.id === definition.id);
            if (definition.value_type.shape === "scalar")
              return (
                <ParameterValueField
                  key={definition.id}
                  label={definition.id}
                  type={definition.value_type.atom}
                  value={value?.shape === "scalar" ? (value.value ?? undefined) : undefined}
                  entities={entities}
                  onChange={(atom) =>
                    setValue(
                      definition.id,
                      atom === undefined
                        ? undefined
                        : { id: definition.id, shape: "scalar", value: atom },
                    )
                  }
                />
              );
            if (definition.value_type.shape !== "table")
              return (
                <p key={definition.id}>
                  {definition.id}: edit this parameter shape through Python.
                </p>
              );
            const table = definition.value_type;
            const rows = value?.shape === "table" ? (value.rows ?? []) : [];
            return (
              <fieldset key={definition.id} className="grid gap-2 rounded border border-line p-3">
                <legend>{definition.id}</legend>
                {value === undefined && <p>Unknown table</p>}
                {rows.map((row, index) => (
                  <div key={index} className="grid gap-2 border-t border-line py-2">
                    {table.columns.map((column) => (
                      <ParameterValueField
                        key={column.id}
                        label={`${definition.id}[${index + 1}].${column.id}`}
                        type={column.value_type}
                        value={row[column.id]}
                        entities={entities}
                        onChange={(atom) => {
                          const next = { ...row };
                          if (atom === undefined) delete next[column.id];
                          else next[column.id] = atom;
                          setValue(definition.id, {
                            id: definition.id,
                            shape: "table",
                            rows: rows.map((item, position) => (position === index ? next : item)),
                          });
                        }}
                      />
                    ))}
                    <button
                      onClick={() =>
                        setValue(definition.id, {
                          id: definition.id,
                          shape: "table",
                          rows: rows.filter((_, position) => position !== index),
                        })
                      }
                    >
                      Remove row {index + 1}
                    </button>
                  </div>
                ))}
                <div className="flex gap-3">
                  <button
                    onClick={() =>
                      setValue(definition.id, {
                        id: definition.id,
                        shape: "table",
                        rows: [...rows, {}],
                      })
                    }
                  >
                    Add row
                  </button>
                  <button onClick={() => setValue(definition.id)}>Mark table unknown</button>
                </div>
              </fieldset>
            );
          })}
        </details>
        <label>
          Draft operator
          <input value={operator} onChange={(event) => patch({ actor: event.target.value })} />
        </label>
        <button
          disabled={draft.conflict || !onSelectConfiguration}
          onClick={async () => {
            const frozen = await draft.capture();
            if (frozen)
              onSelectConfiguration?.(
                adoptionContext && frozen.configuration.kind === "parameters"
                  ? { ...frozen.configuration, setup: adoptionContext.setup }
                  : frozen.configuration,
                { draft_id: frozen.draft_id, revision: frozen.revision },
                adoptionContext
                  ? { kind: "registered_target", ref: adoptionContext.subject.ref }
                  : undefined,
              );
          }}
        >
          {adoptionContext
            ? "Use working inputs, target and setup for next experiment"
            : "Use working inputs for next experiment"}
        </button>
        <p>
          {adoptionContext &&
            "This also selects the resolved target and setup shown above. The selected map object only filters the editor; it does not narrow the experiment target. "}
          This captures a copy for a fresh preview. Existing previews and submitted runs never adopt
          later edits automatically.
        </p>
        <ParameterDraftHistory base={base} onResume={onFork} />
        <label>
          Source or reason for changes
          <input value={note} onChange={(event) => setNote(event.target.value)} />
        </label>
        <label>
          Named branch (optional)
          <input
            list="parameter-branch-names"
            value={branch}
            disabled={!!initial.draft.working_branch}
            onChange={(event) => setBranch(event.target.value)}
          />
        </label>
        <datalist id="parameter-branch-names">
          {heads.data?.items.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
            </option>
          ))}
        </datalist>
        {branch && (
          <p>
            {reviewedGeneration != null
              ? `Update ${branch} from reviewed generation ${reviewedGeneration}.`
              : newBranch
                ? `Create branch ${branch}.`
                : "Reading branch head…"}{" "}
            A concurrent update requires review before retrying.{" "}
            {head && <span> Latest read generation: {head.generation}. </span>}
            <button
              onClick={async () => {
                const requestedBranch = branch;
                const result = await reviewedHead.refetch();
                if (branchRef.current !== requestedBranch) return;
                if (result.data)
                  setBranchReview({
                    branch: requestedBranch,
                    generation: result.data.generation,
                    version: await apiData(
                      apiClient.GET("/api/v1/parameters/revisions/{revision_id}", {
                        params: { path: { revision_id: result.data.revision.revision_id } },
                      }),
                    ),
                  });
                // Creating a new branch has no competing values to inspect.
                else if (result.error instanceof ApiError && result.error.status === 404)
                  patch({ branch_generation: 0 });
              }}
            >
              Review latest branch head
            </button>
          </p>
        )}
        {branchReview && branchReview.branch === branch && (
          <details open>
            <summary>Latest branch version: {branchReview.version.id}</summary>
            <pre>{JSON.stringify(branchReview.version.parameters, null, 2)}</pre>
            <p>
              Keep the entire working table against the reviewed branch head. Values are not merged
              automatically.
            </p>
            <button
              onClick={() => {
                if (branchRef.current === branchReview.branch)
                  patch({ branch_generation: branchReview.generation });
                setBranchReview(undefined);
              }}
            >
              Keep my table after branch review
            </button>
          </details>
        )}
        {(heads.error || (reviewedHead.error && !newBranch)) && (
          <p role="alert">{errorMessage(heads.error ?? reviewedHead.error)}</p>
        )}
        <details>
          <summary>Save a parameter checkpoint</summary>
          <label>
            New version name
            <input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <button
            disabled={
              !name.trim() ||
              !operator.trim() ||
              draft.busy ||
              draft.conflict ||
              (!!branch && reviewedGeneration == null)
            }
            onClick={() => void finish(false)}
          >
            Save parameter version
          </button>
        </details>
        <div className="flex gap-3">
          <button onClick={onCancel}>Close editor</button>
          <button onClick={() => void finish(true)}>Discard draft</button>
          <button disabled={draft.conflict} onClick={() => void fork()}>
            Edit another copy
          </button>
        </div>
      </fieldset>
    </section>
  );
  // Move the presentation only: this editor and its single save queue stay mounted.
  return destination ? createPortal(content, destination.node) : content;
}

function ParameterDraftHistory({
  base,
  onResume,
}: {
  base: ParameterRevision;
  onResume: (view: ParameterDraftView) => void;
}) {
  const [shown, setShown] = useState(false);
  const [before, setBefore] = useState<number>();
  const history = useQuery({
    queryKey: ["parameter-draft-history", base.id, before],
    queryFn: () => parameterDraftHistory(base.id, before),
    enabled: shown,
  });
  return (
    <details
      onToggle={(event) => {
        setShown(event.currentTarget.open);
        if (event.currentTarget.open) void history.refetch();
      }}
    >
      <summary>Recover parameter drafts and history</summary>
      {history.error && <p role="alert">{errorMessage(history.error)}</p>}
      {history.data?.items.map((item) => (
        <details key={item.revision}>
          <summary>
            {item.input.name} · {item.state} · revision {item.revision}
          </summary>
          <pre>{JSON.stringify(item.input, null, 2)}</pre>
          <button onClick={async () => onResume(await readParameterDraft(item.draft_id))}>
            Open current draft
          </button>
          <button
            onClick={async () =>
              onResume(
                await startParameterDraft({
                  working_branch: "",
                  draft_id: crypto.randomUUID(),
                  base: item.base,
                  actor: item.input.actor,
                  copy_from: item.draft_id,
                  copy_revision: item.revision,
                }),
              )
            }
          >
            Edit a copy of these inputs
          </button>
        </details>
      ))}
      {history.data?.next_cursor && (
        <button onClick={() => setBefore(history.data?.next_cursor ?? undefined)}>
          Older drafts
        </button>
      )}
    </details>
  );
}
