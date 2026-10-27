import { useOperatorInput } from "../application/use-operator-input";
import * as Controls from "../../ui/Controls";
import { useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { getHealth } from "../../data/project-api";
import { ApiError } from "../../api-client";
import type { ParameterProposal } from "../../data/parameter-proposals/types";
import { errorMessage } from "../../lib/presentation";
import { secondaryButton } from "../../ui/styles";
import * as api from "./candidate-adoption-api";

export function CandidateAdoption({
  proposal,
  projectId,
}: {
  proposal: ParameterProposal;
  projectId: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="px-3.5 py-3 text-sm">
      <button className={secondaryButton} onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "收起发布审阅" : "审阅发布与记录"}
      </button>
      {open && (
        <AdoptionReview
          key={JSON.stringify([
            projectId,
            proposal.sourceRunId,
            proposal.id,
            proposal.analysisRecordId,
          ])}
          proposal={proposal}
          projectId={projectId}
        />
      )}
    </section>
  );
}

function AdoptionReview({
  proposal,
  projectId,
}: {
  proposal: ParameterProposal;
  projectId: string;
}) {
  const storageKey = `candidate-publication-v2:${JSON.stringify([projectId, proposal.sourceRunId, proposal.id])}`;
  const candidateIdentity = {
    runId: proposal.sourceRunId,
    proposalId: proposal.id,
    analysisId: proposal.analysisRecordId,
    baseHash: proposal.baseContentHash,
    deltas: proposal.deltas,
  };
  const [recovery] = useState(() => {
    try {
      const saved = sessionStorage.getItem(storageKey);
      const stored = saved
        ? (JSON.parse(saved) as {
            projectId: string;
            candidate: typeof candidateIdentity;
            command: api.Publication;
          })
        : undefined;
      if (stored && (stored.projectId !== projectId || !equal(stored.candidate, candidateIdentity)))
        throw new Error("已保存的发布请求属于其他数据空间或候选。请重新打开原数据空间以恢复请求。");
      const command = stored?.command;
      if (stored && !command) throw new Error("无法读取原发布请求。请先检查分支历史再继续。");
      if (
        command &&
        (command.run_id !== proposal.sourceRunId ||
          command.proposal_id !== proposal.id ||
          !command.base ||
          !command.verification ||
          !command.revision_id ||
          !command.name ||
          !Number.isInteger(command.expected_generation))
      )
        throw new Error("无法读取原发布请求。请先检查分支历史再继续。");
      return { command, error: undefined };
    } catch (failure) {
      return { command: undefined, error: errorMessage(failure) };
    }
  });
  const [attempt, setAttempt] = useState(recovery.command);
  const [name, setName] = useState(attempt?.name ?? "");
  const [head, setHead] = useState<api.Branch>();
  const [decision, setDecision] = useState("");
  const { actor, setActor } = useOperatorInput(attempt?.actor);
  const [note, setNote] = useState(attempt?.note ?? "");
  const [busy, setBusy] = useState(false);
  const sending = useRef(false);
  const [error, setError] = useState<string>();
  const [receipt, setReceipt] = useState<api.Branch>();
  const heads = useInfiniteQuery({
    queryKey: ["adoption-branches", projectId],
    queryFn: ({ pageParam }) => api.branches(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const evidence = useInfiniteQuery({
    queryKey: ["candidate-verifications", projectId, proposal.sourceRunId, proposal.id],
    queryFn: ({ pageParam }) => api.verifications(proposal.sourceRunId, proposal.id, pageParam),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const run = useQuery({
    queryKey: ["adoption-source", projectId, proposal.sourceRunId],
    queryFn: () => api.sourceRun(proposal.sourceRunId),
  });
  const records = useQuery({
    queryKey: ["adoption-history", projectId, name],
    enabled: !!name,
    queryFn: () => api.history(name),
  });
  const currentHead = records.data?.items[0] ?? head;
  const current = useQuery({
    queryKey: ["adoption-current", projectId, currentHead?.revision.revision_id],
    enabled: !!currentHead,
    queryFn: () => api.revision(currentHead!.revision.revision_id),
  });
  const draft = useQuery({
    queryKey: ["adoption-working-table", projectId, name],
    enabled: !!name,
    queryFn: () => api.workingTable(name),
  });
  const items = heads.data?.pages.flatMap((page) => page.items) ?? [];
  const checks = evidence.data?.pages.flatMap((page) => page.items) ?? [];
  const selected = checks.find(
    (item) => `${item.reference.analysis_record_id}/${item.reference.output_id}` === decision,
  );
  const source = run.data?.snapshot.config_source;
  const base = source?.kind === "parameter_revision" ? source.parameters : undefined;
  const compatible =
    !!base &&
    !!head &&
    base.revision_id === head.revision.revision_id &&
    base.content_hash === head.revision.content_hash &&
    source?.kind === "parameter_revision" &&
    !source.overrides?.length;
  const moved =
    head && records.isSuccess && records.data.items.at(0)?.generation !== head.generation;
  const readError =
    heads.error ?? evidence.error ?? run.error ?? records.error ?? current.error ?? draft.error;
  const publicationRecords =
    records.data?.items.filter(
      (item) =>
        item.publication?.run_id === proposal.sourceRunId &&
        item.publication.proposal_id === proposal.id,
    ) ?? [];
  const send = async (body: api.Publication) => {
    if (sending.current) return;
    sending.current = true;
    setBusy(true);
    setError(undefined);
    try {
      // Freeze and retain the original request before the first side effect.
      const identity = await getHealth();
      if (identity.projectId !== projectId)
        throw new Error("应用数据空间已变化。请重新打开候选后再发布；原请求已保留。");
      sessionStorage.setItem(
        storageKey,
        JSON.stringify({ projectId, candidate: candidateIdentity, command: body }),
      );
      setAttempt(body);
      const result = await api.publish(body, projectId);
      if (
        result.name !== body.name ||
        result.generation !== body.expected_generation + 1 ||
        result.revision?.revision_id !== body.revision_id ||
        result.publication?.run_id !== body.run_id ||
        result.publication.proposal_id !== body.proposal_id ||
        !equal(result.publication.verification, body.verification)
      )
        throw new ApiError("无法核实发布响应。请恢复原发布请求。");
      setReceipt(result);
      sessionStorage.removeItem(storageKey);
      setAttempt(undefined);
      await records.refetch();
    } catch (failure) {
      setError(errorMessage(failure));
      if (
        failure instanceof ApiError &&
        failure.status &&
        failure.status >= 400 &&
        failure.status < 500 &&
        !failure.message.startsWith("Application data space changed")
      ) {
        sessionStorage.removeItem(storageKey);
        setAttempt(undefined);
      }
    } finally {
      sending.current = false;
      setBusy(false);
    }
  };
  return (
    <div className="mt-3 grid gap-3" aria-label="发布审阅">
      <p>独立验证后，将此精确候选发布到一个已审阅分支。已有运行和已准备实验仍使用原始输入。</p>
      <Controls.Field>
        目标参数分支
        <Controls.Select
          className="block"
          aria-label="目标参数分支"
          value={name}
          disabled={busy || !!attempt || !!receipt}
          onChange={(event) => {
            setName(event.target.value);
            setHead(items.find((item) => item.name === event.target.value));
            setError(undefined);
          }}
        >
          <option value="">选择分支</option>
          {items.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name} · 代次 {item.generation}
            </option>
          ))}
        </Controls.Select>
      </Controls.Field>
      {heads.hasNextPage && (
        <Controls.Button disabled={heads.isFetching} onClick={() => void heads.fetchNextPage()}>
          加载更多分支
        </Controls.Button>
      )}
      {head && (
        <p>
          已审阅分支： {head.name} · 代次 {head.generation} · {head.revision.revision_id}
        </p>
      )}
      {head && !compatible && run.isSuccess && (
        <p role="alert">
          此分支与候选的精确基线不一致，无法发布。请在配置页审阅分支；不会自动合并或变基。
        </p>
      )}
      {moved && <p role="alert">分支自审阅后已前进。使用已记录版本的发布请求将被拒绝。</p>}
      {currentHead && (
        <p>
          当前分支版本： {currentHead.revision.revision_id} · 代次 {currentHead.generation}
        </p>
      )}
      {current.data && (
        <div className="max-h-80 overflow-auto">
          <table className="w-full text-left text-xs">
            <caption>候选基线、已审阅分支与拟议值</caption>
            <thead>
              <tr>
                <th>参数</th>
                <th>原始值</th>
                <th>当前分支</th>
                <th>候选值</th>
                <th>对比</th>
                {draft.data && <th>工作输入（原始内容，不随候选发布）</th>}
              </tr>
            </thead>
            <tbody>
              {proposal.deltas
                .flatMap((delta) => {
                  const value = current.data.parameters.values?.find(
                    (item) => item.id === delta.parameterId,
                  );
                  const working = draft.data?.draft.input.values?.find(
                    (item) => item.id === delta.parameterId,
                  );
                  return delta.cells?.length
                    ? delta.cells.map((cell) => {
                        const currentRow =
                          value?.shape === "table"
                            ? value.rows?.find((row) =>
                                Object.entries(cell.key).every(([key, v]) => equal(row[key], v)),
                              )
                            : undefined;
                        return {
                          id: `${delta.parameterId}[${Object.entries(cell.key)
                            .map(([k, v]) => `${k}=${format(v)}`)
                            .join(", ")}].${cell.field}`,
                          before: cell.before,
                          after: cell.after,
                          current: currentRow?.[cell.field],
                          working: working?.rows?.find((row) =>
                            Object.entries(cell.key).every(([field, expected]) =>
                              rawKeyMatches(row[field]?.text, expected),
                            ),
                          )?.[cell.field],
                        };
                      })
                    : [
                        {
                          id: delta.parameterId,
                          before: delta.before,
                          after: delta.after,
                          current: value?.shape === "scalar" ? value.value : value?.rows,
                          working: working?.shape === "scalar" ? working.value : working?.rows,
                        },
                      ];
                })
                .map((cell) => (
                  <tr key={cell.id}>
                    <th className="p-2">{cell.id}</th>
                    <td className="p-2">{format(cell.before)}</td>
                    <td className="p-2">{format(cell.current)}</td>
                    <td className="p-2">{format(cell.after)}</td>
                    <td>
                      {equal(cell.current, cell.before)
                        ? "与原始值一致"
                        : equal(cell.current, cell.after)
                          ? "已与候选值一致"
                          : "相对候选基线已变化"}
                    </td>
                    {draft.data && (
                      <td className="p-2 whitespace-pre-wrap">{format(cell.working)}</td>
                    )}
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
      {name && (
        <p>
          {name}{" "}
          的工作改动仍保留在工作表中。发布后，请先在那里审阅新的分支头，再保存检查点。发布不会采纳或丢弃这些改动。{" "}
          <a className="underline" href="#configuration">
            打开配置
          </a>
        </p>
      )}
      {draft.data && (
        <details>
          <summary>{name} 已保留的工作表（原始输入）</summary>
          <p>
            草稿 {draft.data.draft.draft_id} · 修订 {draft.data.head_revision}
            。此处不含尚未确认保存的编辑器输入。
          </p>
          <pre className="max-h-48 overflow-auto">
            {JSON.stringify(draft.data.draft.input.values, null, 2)}
          </pre>
        </details>
      )}
      <Controls.Field>
        独立验证
        <Controls.Select
          className="block"
          aria-label="独立验证"
          value={decision}
          disabled={busy || !!attempt || !!receipt}
          onChange={(event) => setDecision(event.target.value)}
        >
          <option value="">选择已有验证记录</option>
          {checks.map((item) => (
            <option
              key={`${item.reference.analysis_record_id}/${item.reference.output_id}`}
              value={`${item.reference.analysis_record_id}/${item.reference.output_id}`}
              disabled={!!item.unavailable_reason}
            >
              {item.title} · {item.reference.analysis_record_id} · {item.reference.output_id}
              {item.unavailable_reason ? "（不可用）" : ""}
            </option>
          ))}
        </Controls.Select>
      </Controls.Field>
      {checks.map((item) => (
        <div key={`${item.reference.analysis_record_id}/${item.reference.output_id}`}>
          <a
            className="underline"
            href={`?analysis=${encodeURIComponent(item.reference.analysis_record_id)}#analyses`}
          >
            {item.title} · {item.reference.output_id}
          </a>
          {item.unavailable_reason && <p>{item.unavailable_reason}</p>}
        </div>
      ))}
      {evidence.hasNextPage && (
        <Controls.Button
          disabled={evidence.isFetching}
          onClick={() => void evidence.fetchNextPage()}
        >
          加载更早的验证记录
        </Controls.Button>
      )}
      {evidence.isSuccess && !checks.some((item) => !item.unavailable_reason) && (
        <p>
          已加载记录中没有符合条件的验证。此候选仍待验证：请使用此精确候选采集独立数据，并保留实验室的验收判定。仅重新分析旧数据不足以完成验证。
        </p>
      )}
      <Controls.Field>
        操作人
        <Controls.Input
          className="block"
          value={actor}
          disabled={busy || !!attempt || !!receipt}
          onChange={(event) => setActor(event.target.value)}
        />
      </Controls.Field>
      <Controls.Field>
        发布备注
        <Controls.Input
          className="block"
          value={note}
          disabled={busy || !!attempt || !!receipt}
          onChange={(event) => setNote(event.target.value)}
        />
      </Controls.Field>
      {readError && <p role="alert">{errorMessage(readError)}</p>}
      {error && <p role="alert">{error}</p>}
      {recovery.error && <p role="alert">{recovery.error}</p>}
      {attempt ? (
        <>
          <p>
            原发布请求：{attempt.name} · 预期代次 {attempt.expected_generation} · 修订{" "}
            {attempt.revision_id}。结果可能未知。仅重试此精确请求以恢复其收据。
          </p>
          <button className={secondaryButton} disabled={busy} onClick={() => void send(attempt)}>
            恢复原发布请求
          </button>
        </>
      ) : (
        !receipt && (
          <button
            className={secondaryButton}
            disabled={
              busy ||
              !compatible ||
              !!moved ||
              !selected ||
              !!selected.unavailable_reason ||
              !actor.trim() ||
              !!readError ||
              !!recovery.error ||
              current.isPending ||
              draft.isPending ||
              records.isPending
            }
            onClick={() =>
              head &&
              base &&
              selected &&
              void send({
                name: head.name,
                expected_generation: head.generation,
                base,
                run_id: proposal.sourceRunId,
                proposal_id: proposal.id,
                verification: selected.reference,
                revision_id: `adopted-${crypto.randomUUID()}`,
                actor: actor.trim(),
                note,
              })
            }
          >
            将已验证候选发布到 {name || "所选分支"}
          </button>
        )
      )}
      {receipt && (
        <p role="status">
          已发布到 {receipt.name} · 代次 {receipt.generation} · {receipt.revision.revision_id}
        </p>
      )}
      <button
        className={secondaryButton}
        disabled={busy}
        onClick={() => {
          void heads.refetch();
          void evidence.refetch();
          if (name) {
            void records.refetch();
            void draft.refetch();
          }
        }}
      >
        刷新依据与记录
      </button>
      <h4>发布记录： {name || "所选分支"}</h4>
      {publicationRecords.map((item) => (
        <div key={item.generation}>
          <p>
            {item.name} · 代次 {item.generation} · {item.revision.revision_id} · {item.actor}
          </p>
          <p>{item.note}</p>
          <a
            className="underline"
            href={`?analysis=${encodeURIComponent(item.publication!.verification.analysis_record_id)}#analyses`}
          >
            查看发布所用验证
          </a>
        </div>
      ))}
      {records.isSuccess && !publicationRecords.length && <p>此分支尚无该候选的发布记录。</p>}
    </div>
  );
}
function format(value: unknown): string {
  return value === undefined ? "缺失" : typeof value === "string" ? value : JSON.stringify(value);
}
function equal(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
  const a = Object.entries(left),
    b = Object.entries(right);
  return (
    a.length === b.length &&
    a.every(([key, value]) => equal(value, (right as Record<string, unknown>)[key]))
  );
}

function rawKeyMatches(text: string | undefined, value: unknown): boolean {
  if (text === undefined) return false;
  if (typeof value === "string") return text === value;
  try {
    return equal(JSON.parse(text), value);
  } catch {
    return false;
  }
}
