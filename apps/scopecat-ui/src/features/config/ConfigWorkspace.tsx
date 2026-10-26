import { useOperatorInput } from "../application/use-operator-input";
import * as Controls from "../../ui/Controls";
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
  const {
    actor: operator,
    setActor: setOperator,
    freshSnapshot: resolveOperator,
    overridden: operatorOverridden,
  } = useOperatorInput();
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
      if (openingObject.current) throw new Error("已有工作参数表正在打开。");
      openingObject.current = true;
      const selection = editorSelection.current;
      const owner = editor.current?.draftId;
      const requireSameEditor = () => {
        if (selection !== editorSelection.current || owner !== editor.current?.draftId)
          throw new Error("打开对象参数时工作编辑器已改变。请保留当前编辑器，从其上下文重试。");
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
          throw new Error("解析上下文后分支已改变。请重新解析；工作输入已保留。");
        const matchesBase = (view: ParameterDraftView) =>
          view.draft.base.revision_id === head.revision.revision_id &&
          view.draft.base.content_hash === head.revision.content_hash;
        if (editing?.view.draft.working_branch === head.name) {
          if (!matchesBase(editing.view) || editor.current?.branchGeneration() !== head.generation)
            throw new Error("已打开的工作表使用其他基线。请先在配置页核对，再解析对象上下文。");
          setObjectContext(context);
          return;
        }
        if (editor.current && !(await editor.current.leave()))
          throw new Error("替换前请先保存已打开的工作表。输入已保留在配置页。");
        requireSameEditor();
        const view = await startParameterDraft({
          draft_id: crypto.randomUUID(),
          base: head.revision,
          actor: await resolveOperator(),
          working_branch: head.name,
          branch_generation: head.generation,
        });
        requireSameEditor();
        if (!matchesBase(view) || view.branch_changed)
          throw new Error("恢复的工作表使用较旧基线。请在配置页打开核对；输入已保留。");
        const next = { base: await loadBase(view), view };
        requireSameEditor();
        if (!(await replaceEditor(next)))
          throw new Error("替换前请先保存已打开的工作表。输入已保留在配置页。");
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
        actor: await resolveOperator(),
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
        actor: await resolveOperator(),
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
        <p role="alert">重新连接后才能保存参数修改。草稿保存完成前请保持此编辑器打开。</p>
      )}
      <header>
        <h2 className="text-lg font-semibold leading-7">实验配置</h2>
        <Controls.Field>
          操作者
          <Controls.Input value={operator} onChange={(event) => setOperator(event.target.value)} />
        </Controls.Field>
      </header>
      <ConfigurationExchange
        operator={operator}
        resolveOperator={resolveOperator}
        operatorOverride={operatorOverridden ? operator : undefined}
        onCreated={async (revision) => {
          await cache.invalidateQueries({ queryKey: ["parameter-revisions"] });
          await cache.invalidateQueries({ queryKey: ["parameter-branches"] });
          setSelected(revision);
        }}
      />
      <SetupPanel
        operator={operator}
        resolveOperator={resolveOperator}
        operatorOverride={operatorOverridden ? operator : undefined}
        onSelectConfiguration={onSelectConfiguration}
      />
      <section
        className="grid gap-3 rounded-lg border border-line bg-panel p-4"
        aria-label="参数版本"
      >
        <h3 className="text-base font-semibold leading-6">工作参数与已保存版本</h3>
        <Controls.Field>
          工作参数分支
          <Controls.Input
            list="working-parameter-branches"
            value={workingBranch}
            onChange={(event) => setWorkingBranch(event.target.value)}
          />
        </Controls.Field>
        <datalist id="working-parameter-branches">
          {branchHeads.data?.items.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
            </option>
          ))}
        </datalist>
        <Controls.Button
          disabled={!workingBranch.trim() || openWorking.isPending}
          onClick={() => openWorking.mutate()}
        >
          打开工作参数表
        </Controls.Button>
        {openWorking.error && <p role="alert">{errorMessage(openWorking.error)}</p>}
        <Controls.Field>
          已保存的参数版本
          <Controls.Select
            value={selected}
            onChange={async (event) => {
              const next = event.target.value;
              if (await replaceEditor()) setSelected(next);
            }}
          >
            <option value="">选择版本</option>
            {versions.data?.items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.id}
              </option>
            ))}
          </Controls.Select>
        </Controls.Field>
        {versions.error && <p role="alert">{errorMessage(versions.error)}</p>}
        {current && (
          <>
            <p>
              {current.actor}
              {current.note ? ` · ${current.note}` : ""}
            </p>
            <div className="flex flex-wrap items-end gap-3">
              <Controls.Button disabled={open.isPending} onClick={() => open.mutate(current)}>
                编辑副本
              </Controls.Button>
              <Controls.Button
                onClick={() =>
                  onSelectConfiguration?.({
                    kind: "parameters",
                    ref: { revision_id: current.id, content_hash: current.content_hash },
                    overrides: [],
                  })
                }
              >
                用于下次实验
              </Controls.Button>
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
      aria-label="编辑参数版本"
      className={
        destination
          ? "grid gap-3 rounded-lg border border-line bg-panel p-4 text-sm max-h-[760px] overflow-y-auto [scrollbar-width:thin]"
          : "grid gap-3 border-t border-line pt-3"
      }
    >
      {destination && objectContext && (
        <header className="rounded-lg border border-line bg-panel-soft p-3">
          <h2 className="font-semibold">工作参数 · {initial.draft.working_branch}</h2>
          <p className="text-sm">
            {objectContext.sample.content.display_name} · 样品版本 {objectContext.sample.revision}
          </p>
          <p className="text-xs text-text-dim break-all">
            基线 {base.id} · setup{" "}
            {objectContext.setupName ?? objectContext.resolution.setup?.revision_id}
          </p>
          {adoptionContext && (
            <p>
              实验目标： {adoptionContext.subject.ref.target_id} · 版本{" "}
              {adoptionContext.subject.ref.revision}
            </p>
          )}
          <p className="text-sm">工作输入</p>
        </header>
      )}
      <p role="status">
        {draft.status === "saved" || (draft.status === "failed" && !draft.hasUnsavedChanges)
          ? "草稿已保存到应用数据"
          : draft.status === "saving"
            ? "正在保存草稿…"
            : draft.status === "conflict"
              ? "存在冲突，你的副本已保留。请核对后继续。"
              : "草稿有未保存的修改"}
      </p>
      {draft.error && <p role="alert">{draft.error}</p>}
      {actionError && <p role="alert">{actionError}</p>}
      {draft.status === "failed" && draft.hasUnsavedChanges && (
        <Controls.Button onClick={() => void draft.flush()}>重试保存草稿</Controls.Button>
      )}
      {draft.conflict && (
        <>
          <Controls.Button
            onClick={async () => setReview(await readParameterDraft(initial.draft.draft_id))}
          >
            核对当前草稿
          </Controls.Button>
          {review && (
            <>
              <pre>{JSON.stringify(review.draft.input, null, 2)}</pre>
              <p>当前草稿： {review.draft.state}</p>
              {review.draft.state === "saved" && (
                <Controls.Button onClick={() => void draft.resolve(review)}>
                  核对后保留我的输入
                </Controls.Button>
              )}
            </>
          )}
        </>
      )}
      {closed && (
        <p>此草稿状态为 {completed?.draft.state ?? initial.draft.state}，输入仍保留在历史中。</p>
      )}
      {completed && (
        <Controls.Button onClick={() => void openCompleted(completed)}>
          打开已保存的参数检查点
        </Controls.Button>
      )}
      {closed && <Controls.Button onClick={onCancel}>关闭编辑器</Controls.Button>}
      {closed && <Controls.Button onClick={() => void fork()}>编辑另一份副本</Controls.Button>}
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
          <summary>全部参数 · 高级</summary>
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
              return <p key={definition.id}>{definition.id}：请通过 Python 编辑此参数结构。</p>;
            const table = definition.value_type;
            const rows = value?.shape === "table" ? (value.rows ?? []) : [];
            return (
              <fieldset key={definition.id} className="grid gap-2 rounded border border-line p-3">
                <legend>{definition.id}</legend>
                {value === undefined && <p>未知表格</p>}
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
                    <Controls.Button
                      onClick={() =>
                        setValue(definition.id, {
                          id: definition.id,
                          shape: "table",
                          rows: rows.filter((_, position) => position !== index),
                        })
                      }
                    >
                      移除行 {index + 1}
                    </Controls.Button>
                  </div>
                ))}
                <div className="flex flex-wrap items-end gap-3">
                  <Controls.Button
                    onClick={() =>
                      setValue(definition.id, {
                        id: definition.id,
                        shape: "table",
                        rows: [...rows, {}],
                      })
                    }
                  >
                    添加行
                  </Controls.Button>
                  <Controls.Button onClick={() => setValue(definition.id)}>
                    将表格标记为未知
                  </Controls.Button>
                </div>
              </fieldset>
            );
          })}
        </details>
        <Controls.Field>
          草稿操作者
          <Controls.Input
            value={operator}
            onChange={(event) => patch({ actor: event.target.value })}
          />
        </Controls.Field>
        <Controls.Button
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
          {adoptionContext ? "将工作输入、目标和 setup 用于下次实验" : "将工作输入用于下次实验"}
        </Controls.Button>
        <p>
          {adoptionContext &&
            "同时选择上方已解析的目标和 setup。地图中的选中对象仅筛选编辑内容，不会缩小实验目标范围。"}
          下次准备将使用此处显示的输入。
        </p>
        <ParameterDraftHistory base={base} onResume={onFork} />
        <Controls.Field>
          修改来源或原因
          <Controls.Input value={note} onChange={(event) => setNote(event.target.value)} />
        </Controls.Field>
        <Controls.Field>
          命名分支（可选）
          <Controls.Input
            list="parameter-branch-names"
            value={branch}
            disabled={!!initial.draft.working_branch}
            onChange={(event) => setBranch(event.target.value)}
          />
        </Controls.Field>
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
              ? `基于已核对的 generation ${reviewedGeneration} 更新分支 ${branch}。`
              : newBranch
                ? `创建分支 ${branch}。`
                : "正在读取分支最新版本…"}{" "}
            发生并发更新，请先核对再重试。{" "}
            {head && <span> 最近读取的 generation： {head.generation}. </span>}
            <Controls.Button
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
              核对分支最新版本
            </Controls.Button>
          </p>
        )}
        {branchReview && branchReview.branch === branch && (
          <details open>
            <summary>分支最新版本： {branchReview.version.id}</summary>
            <pre>{JSON.stringify(branchReview.version.parameters, null, 2)}</pre>
            <p>以核对过的分支最新版本为基线保留整个工作表。数值不会自动合并。</p>
            <Controls.Button
              onClick={() => {
                if (branchRef.current === branchReview.branch)
                  patch({ branch_generation: branchReview.generation });
                setBranchReview(undefined);
              }}
            >
              核对分支后保留我的表格
            </Controls.Button>
          </details>
        )}
        {(heads.error || (reviewedHead.error && !newBranch)) && (
          <p role="alert">{errorMessage(heads.error ?? reviewedHead.error)}</p>
        )}
        <details>
          <summary>保存参数检查点</summary>
          <Controls.Field>
            新版本名称
            <Controls.Input value={name} onChange={(event) => setName(event.target.value)} />
          </Controls.Field>
          <Controls.Button
            disabled={
              !name.trim() ||
              !operator.trim() ||
              draft.busy ||
              draft.conflict ||
              (!!branch && reviewedGeneration == null)
            }
            onClick={() => void finish(false)}
          >
            保存参数版本
          </Controls.Button>
        </details>
        <div className="flex flex-wrap items-end gap-3">
          <Controls.Button onClick={onCancel}>关闭编辑器</Controls.Button>
          <Controls.Button onClick={() => void finish(true)}>丢弃草稿</Controls.Button>
          <Controls.Button disabled={draft.conflict} onClick={() => void fork()}>
            编辑另一份副本
          </Controls.Button>
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
      <summary>恢复参数草稿与历史</summary>
      {history.error && <p role="alert">{errorMessage(history.error)}</p>}
      {history.data?.items.map((item) => (
        <details key={item.revision}>
          <summary>
            {item.input.name} · {item.state} · 版本 {item.revision}
          </summary>
          <pre>{JSON.stringify(item.input, null, 2)}</pre>
          <Controls.Button onClick={async () => onResume(await readParameterDraft(item.draft_id))}>
            打开当前草稿
          </Controls.Button>
          <Controls.Button
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
            编辑这些输入的副本
          </Controls.Button>
        </details>
      ))}
      {history.data?.next_cursor && (
        <Controls.Button onClick={() => setBefore(history.data?.next_cursor ?? undefined)}>
          更早的草稿
        </Controls.Button>
      )}
    </details>
  );
}
