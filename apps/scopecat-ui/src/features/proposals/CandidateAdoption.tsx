import { useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ApiError } from "../../api-client";
import type { ParameterProposal } from "../../data/parameter-proposals/types";
import { errorMessage } from "../../lib/presentation";
import { secondaryButton } from "../../ui/styles";
import * as api from "./candidate-adoption-api";

export function CandidateAdoption({ proposal }: { proposal: ParameterProposal }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="px-3.5 py-3 text-sm">
      <button className={secondaryButton} onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "Hide adoption review" : "Review adoption and records"}
      </button>
      {open && <AdoptionReview proposal={proposal} />}
    </section>
  );
}

function AdoptionReview({ proposal }: { proposal: ParameterProposal }) {
  const storageKey = `candidate-publication:${proposal.sourceRunId}:${proposal.id}`;
  const [recovery] = useState(() => {
    try {
      const saved = sessionStorage.getItem(storageKey);
      const command = saved ? (JSON.parse(saved) as api.Publication) : undefined;
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
        throw new Error(
          "Original adoption request is unreadable. Inspect branch history before continuing.",
        );
      return { command, error: undefined };
    } catch (failure) {
      return { command: undefined, error: errorMessage(failure) };
    }
  });
  const [attempt, setAttempt] = useState(recovery.command);
  const [name, setName] = useState(attempt?.name ?? "");
  const [head, setHead] = useState<api.Branch>();
  const [decision, setDecision] = useState("");
  const [actor, setActor] = useState(attempt?.actor ?? "local-operator");
  const [note, setNote] = useState(attempt?.note ?? "");
  const [busy, setBusy] = useState(false);
  const sending = useRef(false);
  const [error, setError] = useState<string>();
  const [receipt, setReceipt] = useState<api.Branch>();
  const heads = useInfiniteQuery({
    queryKey: ["adoption-branches"],
    queryFn: ({ pageParam }) => api.branches(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const evidence = useInfiniteQuery({
    queryKey: ["candidate-verifications", proposal.sourceRunId, proposal.id],
    queryFn: ({ pageParam }) => api.verifications(proposal.sourceRunId, proposal.id, pageParam),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const run = useQuery({
    queryKey: ["adoption-source", proposal.sourceRunId],
    queryFn: () => api.sourceRun(proposal.sourceRunId),
  });
  const records = useQuery({
    queryKey: ["adoption-history", name],
    enabled: !!name,
    queryFn: () => api.history(name),
  });
  const currentHead = records.data?.items[0] ?? head;
  const current = useQuery({
    queryKey: ["adoption-current", currentHead?.revision.revision_id],
    enabled: !!currentHead,
    queryFn: () => api.revision(currentHead!.revision.revision_id),
  });
  const draft = useQuery({
    queryKey: ["adoption-working-table", name],
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
      sessionStorage.setItem(storageKey, JSON.stringify(body));
      setAttempt(body);
      const result = await api.publish(body);
      if (
        result.name !== body.name ||
        result.generation !== body.expected_generation + 1 ||
        result.revision?.revision_id !== body.revision_id ||
        result.publication?.run_id !== body.run_id ||
        result.publication.proposal_id !== body.proposal_id ||
        !equal(result.publication.verification, body.verification)
      )
        throw new ApiError(
          "The adoption response could not be verified. Recover the original request.",
        );
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
        failure.status < 500
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
    <div className="mt-3 grid gap-3" aria-label="Adoption review">
      <p>
        Adoption publishes this exact candidate to one reviewed branch after independent
        verification. Existing runs and prepared experiments keep their original inputs.
      </p>
      <label>
        Target parameter branch
        <select
          className="block rounded border p-2"
          aria-label="Target parameter branch"
          value={name}
          disabled={busy || !!attempt || !!receipt}
          onChange={(event) => {
            setName(event.target.value);
            setHead(items.find((item) => item.name === event.target.value));
            setError(undefined);
          }}
        >
          <option value="">Choose a branch</option>
          {items.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name} · generation {item.generation}
            </option>
          ))}
        </select>
      </label>
      {heads.hasNextPage && (
        <button disabled={heads.isFetching} onClick={() => void heads.fetchNextPage()}>
          Load more branches
        </button>
      )}
      {head && (
        <p>
          Reviewed branch: {head.name} · generation {head.generation} · {head.revision.revision_id}
        </p>
      )}
      {head && !compatible && run.isSuccess && (
        <p role="alert">
          This branch is not at the candidate's exact baseline. Adoption is blocked; review the
          branch in Configuration. No automatic merge or rebase is performed.
        </p>
      )}
      {moved && (
        <p role="alert">
          The branch has moved since review. Adoption with the captured version will be rejected.
        </p>
      )}
      {currentHead && (
        <p>
          Current branch version: {currentHead.revision.revision_id} · generation{" "}
          {currentHead.generation}
        </p>
      )}
      {current.data && (
        <div className="max-h-80 overflow-auto">
          <table className="w-full text-left text-xs">
            <caption>Candidate baseline, reviewed branch and proposed values</caption>
            <thead>
              <tr>
                <th>Parameter</th>
                <th>Original</th>
                <th>Current branch</th>
                <th>Candidate</th>
                <th>Comparison</th>
                {draft.data && <th>Working input (raw, not adopted)</th>}
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
                        ? "Matches original"
                        : equal(cell.current, cell.after)
                          ? "Already matches candidate"
                          : "Changed since candidate"}
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
          Working edits on {name} remain in their working table. After publication, review the new
          branch head there before checkpointing. Publication does not adopt or discard those edits.{" "}
          <a className="underline" href="#configuration">
            Open Configuration
          </a>
        </p>
      )}
      {draft.data && (
        <details>
          <summary>Retained working table for {name} (raw input)</summary>
          <p>
            Draft {draft.data.draft.draft_id} · revision {draft.data.head_revision}. Unacknowledged
            editor input is not included here.
          </p>
          <pre className="max-h-48 overflow-auto">
            {JSON.stringify(draft.data.draft.input.values, null, 2)}
          </pre>
        </details>
      )}
      <label>
        Independent verification
        <select
          className="block rounded border p-2"
          aria-label="Independent verification"
          value={decision}
          disabled={busy || !!attempt || !!receipt}
          onChange={(event) => setDecision(event.target.value)}
        >
          <option value="">Choose a retained verification</option>
          {checks.map((item) => (
            <option
              key={`${item.reference.analysis_record_id}/${item.reference.output_id}`}
              value={`${item.reference.analysis_record_id}/${item.reference.output_id}`}
              disabled={!!item.unavailable_reason}
            >
              {item.title} · {item.reference.analysis_record_id} · {item.reference.output_id}
              {item.unavailable_reason ? " (unavailable)" : ""}
            </option>
          ))}
        </select>
      </label>
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
        <button disabled={evidence.isFetching} onClick={() => void evidence.fetchNextPage()}>
          Load older verification records
        </button>
      )}
      {evidence.isSuccess && !checks.some((item) => !item.unavailable_reason) && (
        <p>
          No eligible verification in the loaded records. Keep this candidate pending verification:
          collect independent data using this exact candidate and retain your laboratory's
          acceptance decision. Reanalysis of old data is insufficient.
        </p>
      )}
      <label>
        Operator
        <input
          className="block rounded border p-2"
          value={actor}
          disabled={busy || !!attempt || !!receipt}
          onChange={(event) => setActor(event.target.value)}
        />
      </label>
      <label>
        Adoption note
        <input
          className="block rounded border p-2"
          value={note}
          disabled={busy || !!attempt || !!receipt}
          onChange={(event) => setNote(event.target.value)}
        />
      </label>
      {readError && <p role="alert">{errorMessage(readError)}</p>}
      {error && <p role="alert">{error}</p>}
      {recovery.error && <p role="alert">{recovery.error}</p>}
      {attempt ? (
        <>
          <p>
            Original adoption: {attempt.name} · expected generation {attempt.expected_generation} ·
            revision {attempt.revision_id}. Its outcome may be unknown. Retry only this exact
            request to recover its receipt.
          </p>
          <button className={secondaryButton} disabled={busy} onClick={() => void send(attempt)}>
            Recover original adoption
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
            Adopt verified candidate into {name || "selected branch"}
          </button>
        )
      )}
      {receipt && (
        <p role="status">
          Adopted into {receipt.name} · generation {receipt.generation} ·{" "}
          {receipt.revision.revision_id}
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
        Refresh evidence and records
      </button>
      <h4>Adoption records for {name || "selected branch"}</h4>
      {publicationRecords.map((item) => (
        <div key={item.generation}>
          <p>
            {item.name} · generation {item.generation} · {item.revision.revision_id} · {item.actor}
          </p>
          <p>{item.note}</p>
          <a
            className="underline"
            href={`?analysis=${encodeURIComponent(item.publication!.verification.analysis_record_id)}#analyses`}
          >
            Open adopted verification
          </a>
        </div>
      ))}
      {records.isSuccess && !publicationRecords.length && (
        <p>No adoption of this candidate recorded on this branch.</p>
      )}
    </div>
  );
}
function format(value: unknown): string {
  return value === undefined
    ? "Missing"
    : typeof value === "string"
      ? value
      : JSON.stringify(value);
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
