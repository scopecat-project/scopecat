import type { MethodResponse } from "openapi-fetch";
import { normalizeSelection, reviewedForRequest } from "./scientific-selection";
import type { SubmissionRequest } from "./launch-submission";
import type { Dispatch, SetStateAction } from "react";
import { useEffect, useRef, useState } from "react";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
import type { LaunchDraft } from "./LaunchDraft";
import type { ControlDrafts } from "./ControlFields";

type Input = components["schemas"]["LaunchDraftInput-Input"];
export type DraftRecord = components["schemas"]["LaunchDraftRecord"];
export type AttemptRecord = MethodResponse<
  typeof apiClient,
  "get",
  "/api/v1/launch-attempts"
>["items"][number];
export function attemptRequest(record: AttemptRecord): SubmissionRequest {
  return {
    ...record.request,
    selection: normalizeSelection(record.request.selection),
    reviewed: record.request.reviewed ? reviewedForRequest(record.request.reviewed) : undefined,
  };
}
type Save = components["schemas"]["LaunchDraftSave"];
export const readDraft = (workspace_id: string, experiment: string) =>
  apiData(apiClient.POST("/api/v1/launch-drafts/read", { body: { workspace_id, experiment } }));
export const saveDraft = (body: Save) =>
  apiData(apiClient.POST("/api/v1/launch-drafts/save", { body }));
export const draftHistory = (before?: number) =>
  apiData(apiClient.GET("/api/v1/launch-drafts", { params: { query: { before, limit: 50 } } }));
export const attemptHistory = (before?: number) =>
  apiData(apiClient.GET("/api/v1/launch-attempts", { params: { query: { before, limit: 50 } } }));
export const resolveAttempt = (sequence: number) =>
  apiData(
    apiClient.GET("/api/v1/launch-attempts/{sequence}/resolve", { params: { path: { sequence } } }),
  );

export function rawInput(draft: LaunchDraft): Input {
  return {
    declaration: JSON.parse(draft.definition) as Input["declaration"],
    code_revision: draft.sourceBaseline ?? draft.codeRevision,
    pinned_source: Boolean(draft.codeRevision),
    values: draft.values,
    controls: Object.fromEntries(
      Object.entries(draft.controls).map(([id, value]) => [
        id,
        { value: "", values: "", start: "", stop: "", points: "", ...value },
      ]),
    ),
    selection: draft.selection,
    actor: draft.actor,
    collection: draft.collection,
    working_input: draft.workingInput ? { ...draft.workingInput } : undefined,
    plan: draft.plan ? JSON.parse(JSON.stringify(draft.plan)) : undefined,
    plan_dirty: draft.planDirty ?? false,
    handoff: draft.handoff ? JSON.parse(JSON.stringify(draft.handoff)) : undefined,
  };
}
export function restoreDraft(record: DraftRecord, revision: number): LaunchDraft {
  const input = record.input;
  return {
    workspaceId: record.target.workspace_id,
    experiment: record.target.experiment,
    definition: JSON.stringify(input.declaration),
    controlDefinition: "",
    values: input.values,
    controls: input.controls as ControlDrafts,
    selection: normalizeSelection(input.selection),
    actor: input.actor,
    collection: input.collection ?? undefined,
    codeRevision: input.pinned_source ? (input.code_revision ?? undefined) : undefined,
    sourceBaseline: input.code_revision ?? undefined,
    workingInput: input.working_input as LaunchDraft["workingInput"],
    plan: input.plan as LaunchDraft["plan"],
    planDirty: input.plan_dirty,
    handoff: input.handoff as LaunchDraft["handoff"],
    revision,
    pending: false,
    error: "",
    notice: "Recovered experiment input. Review the retained baseline and preview before starting.",
    needsReview: true,
  };
}

interface Session {
  target: string;
  revision: number;
  head?: DraftRecord;
  ready: boolean;
  pending: number;
  queued: string;
  queue: Promise<void>;
  failed?: Save;
  conflict: boolean;
  hydrating?: string;
  wake?: () => void;
  flushing?: boolean;
}
export function useLaunchRecovery(
  draft: LaunchDraft | undefined,
  setDraft: Dispatch<SetStateAction<LaunchDraft | undefined>>,
) {
  const session = useRef<Session | undefined>(undefined);
  const [unfinished] = useState(() => new Map<string, Session>());
  const [visibleSession, setVisibleSession] = useState<Session>();
  const explicitInput = useRef<string | undefined>(undefined);
  const [reload, setReload] = useState(0);
  const requestedCopy = useRef<DraftRecord | undefined>(undefined);
  const [status, setStatus] = useState("Loading saved experiment input…");
  const [epoch, setEpoch] = useState(0);
  const target =
    draft?.experiment && draft.workspaceId
      ? JSON.stringify([draft.workspaceId, draft.experiment])
      : "";
  const encoded = draft?.definition ? JSON.stringify(rawInput(draft)) : "";
  useEffect(() => {
    if (!target) return;
    const [workspace, experiment] = JSON.parse(target) as [string, string];
    const cached = unfinished.get(target);
    if (cached && (cached.failed || cached.pending > 0)) {
      requestedCopy.current = undefined;
      explicitInput.current = undefined;
      session.current = cached;
      const restored = restoreDraft(
        {
          revision: cached.revision,
          target: { workspace_id: workspace, experiment },
          input: JSON.parse(cached.queued),
          state: "saved",
          created_at: "",
        },
        1,
      );
      cached.hydrating = JSON.stringify(rawInput(restored));
      setDraft((previous) => ({ ...restored, revision: (previous?.revision ?? 0) + 1 }));
      queueMicrotask(() => {
        setStatus(
          cached.failed
            ? "Input not confirmed saved. Your local copy is retained; retry saving before switching again."
            : "Saving experiment input…",
        );
        setEpoch((value) => value + 1);
      });
      return;
    }
    const current: Session = {
      target,
      revision: 0,
      ready: false,
      pending: 0,
      queued: "",
      queue: Promise.resolve(),
      conflict: false,
    };
    session.current = current;
    unfinished.set(target, current);
    queueMicrotask(() => {
      if (session.current === current) setStatus("Loading saved experiment input…");
    });
    let active = true;
    void readDraft(workspace, experiment)
      .then(({ head }) => {
        current.revision = head?.revision ?? 0;
        current.head = head ?? undefined;
        if (!active) return;
        const pendingCopy = requestedCopy.current;
        const copy =
          pendingCopy?.target.workspace_id === workspace &&
          pendingCopy.target.experiment === experiment
            ? pendingCopy
            : undefined;
        const selected =
          copy?.target.workspace_id === workspace && copy.target.experiment === experiment
            ? copy
            : head?.state === "saved"
              ? head
              : undefined;
        if (copy) requestedCopy.current = undefined;
        if (copy && selected === copy) current.revision = copy.revision;
        if (selected && explicitInput.current !== target) {
          if (!copy) current.queued = JSON.stringify(rawInput(restoreDraft(selected, 1)));
          setDraft((previous) =>
            previous?.workspaceId === workspace && previous.experiment === experiment
              ? restoreDraft(selected, previous.revision + 1)
              : previous,
          );
        }
        if (explicitInput.current === target) explicitInput.current = undefined;
        current.ready = true;
        setStatus(
          head?.state === "saved"
            ? "Experiment input saved in application data."
            : "Saving experiment input…",
        );
        setEpoch((value) => value + 1);
      })
      .catch((error) => {
        if (active)
          setStatus(
            `Input recovery unavailable: ${String(error)}. Reopen this experiment to retry.`,
          );
      });
    return () => {
      active = false;
    };
  }, [target, setDraft, reload, unfinished]);
  useEffect(() => {
    const current = session.current;
    if (current?.hydrating) {
      if (encoded !== current.hydrating) return;
      current.hydrating = undefined;
      setEpoch((value) => value + 1);
    }
    if (!encoded || !current?.ready || current.target !== target || current.queued === encoded)
      return;
    current.queued = encoded;
    if (current.failed || current.pending) return;
    current.pending = 1;
    setStatus("Saving experiment input…");
    current.queue = (async () => {
      try {
        while (!current.failed) {
          const candidate = current.queued;
          if (!current.flushing) {
            await new Promise<void>((resolve) => {
              const timer = setTimeout(resolve, 250);
              current.wake = () => {
                clearTimeout(timer);
                resolve();
              };
            });
            current.wake = undefined;
            if (candidate !== current.queued) continue;
          }
          const sent = current.queued;
          const [workspace_id, experiment] = JSON.parse(current.target) as [string, string];
          const command: Save = {
            operation_id: crypto.randomUUID(),
            expected_revision: current.revision,
            target: { workspace_id, experiment },
            input: JSON.parse(sent) as Input,
            discard: false,
          };
          try {
            const result = await saveDraft(command);
            current.head = result.head ?? undefined;
            current.conflict = result.saved?.state === "conflict";
            if (!current.conflict) current.revision = result.saved!.revision;
            if (sent !== current.queued) continue;
            if (session.current === current)
              setStatus(
                current.conflict
                  ? "Another window changed this experiment. Your edits are retained as a separate conflict copy."
                  : "Experiment input saved in application data.",
              );
            break;
          } catch (error) {
            current.failed = command;
            if (session.current === current)
              setStatus(
                `Input not confirmed saved: ${String(error)}. Keep this window open and retry saving.`,
              );
          }
        }
      } finally {
        current.pending = 0;
        current.flushing = false;
        setEpoch((value) => value + 1);
      }
    })();
  }, [encoded, target, epoch]);
  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (active) setVisibleSession(session.current ? { ...session.current } : undefined);
    });
    return () => {
      active = false;
    };
  }, [epoch, target, status]);
  useEffect(
    () => () => {
      for (const pending of unfinished.values()) {
        if (pending.pending) {
          pending.flushing = true;
          pending.wake?.();
        }
      }
    },
    [unfinished],
  );
  const current = visibleSession;
  return {
    status,
    canLeave: () => !session.current?.failed && !session.current?.hydrating,
    unsavedTargets: [...unfinished.values()]
      .filter((item) => item.failed)
      .map((item) => JSON.parse(item.target) as [string, string]),
    keepExplicitInput(workspace: string, experiment: string) {
      const selected = JSON.stringify([workspace, experiment]);
      const cached = unfinished.get(selected);
      if (cached?.failed || cached?.pending || cached?.hydrating) {
        setStatus(
          "Return to this experiment and finish saving its retained input before replacing it.",
        );
        return false;
      }
      if (session.current?.target !== selected || !session.current.ready)
        explicitInput.current = selected;
      return true;
    },
    selectCopy(record: DraftRecord) {
      const cached = unfinished.get(
        JSON.stringify([record.target.workspace_id, record.target.experiment]),
      );
      if (session.current?.pending || cached?.failed || cached?.pending || cached?.hydrating) {
        setStatus("Wait for the current input save before opening a historical copy.");
        return false;
      }
      requestedCopy.current = record;
      if (
        session.current?.target ===
        JSON.stringify([record.target.workspace_id, record.target.experiment])
      ) {
        session.current.revision = record.revision;
        session.current.queued = "";
        requestedCopy.current = undefined;
      }
      return true;
    },
    writing: Boolean(current?.pending),
    ready: current?.target === target && current.ready && !current.hydrating,
    conflict: current?.target === target && current.conflict,
    head: current?.head,
    async flush() {
      const activeSession = session.current;
      if (!activeSession?.ready || activeSession.hydrating || activeSession.target !== target)
        throw new Error("Wait for input recovery before submitting.");
      if (activeSession.pending) {
        activeSession.flushing = true;
        activeSession.wake?.();
      }
      await activeSession.queue;
      if (activeSession.failed || activeSession.conflict || activeSession.queued !== encoded)
        throw new Error("Resolve and save the current input before submitting.");
    },
    async retry() {
      const activeSession = session.current;
      if (!activeSession?.ready) {
        setReload((value) => value + 1);
        return;
      }
      if (!activeSession.failed || activeSession.pending) return;
      const command = activeSession.failed;
      activeSession.pending = 1;
      activeSession.queue = (async () => {
        try {
          const result = await saveDraft(command);
          activeSession.failed = undefined;
          activeSession.head = result.head ?? undefined;
          activeSession.conflict = result.saved?.state === "conflict";
          if (!activeSession.conflict) activeSession.revision = result.saved!.revision;
          activeSession.queued = "";
        } catch (error) {
          setStatus(`Input not confirmed saved: ${String(error)}`);
        } finally {
          activeSession.pending = 0;
          setEpoch((value) => value + 1);
        }
      })();
      await activeSession.queue;
    },
    adoptLocal() {
      const activeSession = session.current;
      if (!activeSession?.conflict || activeSession.pending) return;
      activeSession.revision = activeSession.head?.revision ?? 0;
      activeSession.conflict = false;
      activeSession.queued = "";
      setEpoch((value) => value + 1);
    },
  };
}
