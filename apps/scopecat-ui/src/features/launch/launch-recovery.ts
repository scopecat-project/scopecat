import {
  retainLaunchCloseParticipant,
  launchCloseBlocked,
  resetLaunchCloseWindowForTests,
} from "./launch-close-barrier";
import type { MethodResponse } from "openapi-fetch";
import { normalizeSelection, reviewedForRequest } from "./scientific-selection";
import type { SubmissionRequest } from "./launch-submission";
import type { Dispatch, SetStateAction } from "react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
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
export const readDraft = (workspace_id: string, experiment: string, draft_id = "default") =>
  apiData(
    apiClient.POST("/api/v1/launch-drafts/read", { body: { workspace_id, experiment, draft_id } }),
  );
export const listDrafts = (workspace_id: string, experiment: string, before?: number) =>
  apiData(
    apiClient.POST("/api/v1/launch-drafts/list", {
      body: { workspace_id, experiment, draft_id: "default" },
      params: { query: { before, limit: 50 } },
    }),
  );
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
    draft_name: draft.draftName ?? "Draft",
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
    manual_line: draft.manualLine,
    line_source: draft.lineSource,
    working_input: draft.workingInput ? { ...draft.workingInput } : undefined,
    plan: draft.plan ? JSON.parse(JSON.stringify(draft.plan)) : undefined,
    plan_dirty: draft.planDirty ?? false,
    handoff: draft.handoff ? JSON.parse(JSON.stringify(draft.handoff)) : undefined,
  };
}
export function restoreDraft(record: DraftRecord, revision: number): LaunchDraft {
  const input = record.input;
  return {
    draftId: record.target.draft_id ?? "default",
    draftName: input.draft_name ?? "Draft",
    workspaceId: record.target.workspace_id,
    experiment: record.target.experiment,
    definition: JSON.stringify(input.declaration),
    controlDefinition: "",
    values: input.values,
    controls: input.controls as ControlDrafts,
    selection: normalizeSelection(input.selection),
    actor: input.actor,
    collection: input.collection ?? undefined,
    manualLine: input.manual_line ?? undefined,
    lineSource: input.line_source,
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
  confirmed: string;
  localInput?: string;
  queue: Promise<void>;
  failed?: Save;
  conflict: boolean;
  hydrating?: string;
  wake?: () => void;
  flushing?: boolean;
}
const windowSessions = new Map<string, Map<string, Session>>();
/** Tests destroy the whole window between cases; provider unmount must retain sessions. */
export function resetLaunchRecoveryWindowForTests() {
  windowSessions.clear();
  resetLaunchCloseWindowForTests();
}

export function useLaunchRecovery(
  draft: LaunchDraft | undefined,
  setDraft: Dispatch<SetStateAction<LaunchDraft | undefined>>,
  close?: { owner: string; getDraft: () => LaunchDraft | undefined; revoke: () => void },
) {
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      // Persistence may finish after unmount; it must not notify the old UI.
      mounted.current = false;
    };
  }, []);
  const session = useRef<Session | undefined>(undefined);
  const [unfinished] = useState(() => {
    if (!close) return new Map<string, Session>();
    const retained = windowSessions.get(close.owner) ?? new Map<string, Session>();
    windowSessions.set(close.owner, retained);
    return retained;
  });
  const liveInput = useRef({ draft, close });
  useLayoutEffect(() => {
    liveInput.current = { draft, close };
  });
  const [visibleSession, setVisibleSession] = useState<Session>();
  const explicitInput = useRef<string | undefined>(undefined);
  const [reload, setReload] = useState(0);
  const requestedCopy = useRef<DraftRecord | undefined>(undefined);
  const [status, setStatus] = useState("Loading saved experiment input…");
  const [epoch, setEpoch] = useState(0);
  const target =
    draft?.experiment && draft.workspaceId
      ? JSON.stringify([draft.workspaceId, draft.experiment, draft.draftId ?? "default"])
      : "";
  const encoded = draft?.definition ? JSON.stringify(rawInput(draft)) : "";
  function capturePendingInput(current: Session) {
    if (current.ready) return;
    const latest = liveInput.current.close
      ? liveInput.current.close.getDraft()
      : liveInput.current.draft;
    if (
      !latest?.definition ||
      JSON.stringify([latest.workspaceId, latest.experiment, latest.draftId ?? "default"]) !==
        current.target
    )
      return;
    const input = JSON.stringify(rawInput(latest));
    if (current.localInput !== undefined || explicitInput.current === current.target)
      current.localInput = input;
  }
  useLayoutEffect(() => {
    if (session.current) capturePendingInput(session.current);
  });
  useEffect(() => {
    if (!target) return;
    const [workspace, experiment, draft_id] = JSON.parse(target) as [string, string, string];
    const cached = unfinished.get(target);
    if (cached && (cached.failed || cached.conflict || cached.pending > 0)) {
      requestedCopy.current = undefined;
      explicitInput.current = undefined;
      session.current = cached;
      const restored = restoreDraft(
        {
          revision: cached.revision,
          target: { workspace_id: workspace, experiment, draft_id },
          input: JSON.parse(cached.queued),
          state: "saved",
          created_at: "",
        },
        1,
      );
      cached.hydrating = JSON.stringify(rawInput(restored));
      setDraft((previous) => ({ ...restored, revision: (previous?.revision ?? 0) + 1 }));
      queueMicrotask(() => {
        if (!mounted.current || session.current !== cached) return;
        setStatus(
          cached.failed
            ? "Input not confirmed saved. Your local copy is retained; retry saving before switching again."
            : cached.conflict
              ? "Another window changed this experiment. Your conflict copy is retained."
              : "Saving experiment input…",
        );
        setEpoch((value) => value + 1);
      });
      return;
    }
    // A detached read may own edits even though no server revision was available.
    // Keep that input when retrying recovery instead of replacing it with the head.
    const retainedInput = cached && !cached.ready ? cached.localInput : undefined;
    if (retainedInput) {
      explicitInput.current = target;
      setDraft((previous) =>
        restoreDraft(
          {
            revision: cached!.revision,
            target: { workspace_id: workspace, experiment, draft_id },
            input: JSON.parse(retainedInput),
            state: "saved",
            created_at: "",
          },
          (previous?.revision ?? 0) + 1,
        ),
      );
    }
    const initial = liveInput.current.draft;
    const initialInput = initial?.definition ? JSON.stringify(rawInput(initial)) : "";
    const current: Session = {
      target,
      revision: 0,
      ready: false,
      pending: 0,
      queued: "",
      confirmed: "",
      localInput:
        retainedInput ??
        (explicitInput.current === target || requestedCopy.current ? initialInput : undefined),
      queue: Promise.resolve(),
      conflict: false,
    };
    session.current = current;
    unfinished.set(target, current);
    queueMicrotask(() => {
      if (mounted.current && session.current === current)
        setStatus("Loading saved experiment input…");
    });
    let active = true;
    void readDraft(workspace, experiment, draft_id)
      .then(({ head }) => {
        capturePendingInput(current);
        // Local input adopted before hydration has no reviewed server base. Let
        // the existing CAS retain a conflict instead of overwriting a newer head.
        if (current.localInput === undefined) current.revision = head?.revision ?? 0;
        current.head = head ?? undefined;
        if (!active) return;
        const pendingCopy = requestedCopy.current;
        const copy =
          pendingCopy?.target.workspace_id === workspace &&
          pendingCopy.target.experiment === experiment &&
          (pendingCopy.target.draft_id ?? "default") === draft_id
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
        if (selected && explicitInput.current !== target && current.localInput === undefined) {
          if (!copy) {
            current.queued = JSON.stringify(rawInput(restoreDraft(selected, 1)));
            current.confirmed = current.queued;
          }
          setDraft((previous) =>
            previous?.workspaceId === workspace &&
            previous.experiment === experiment &&
            (previous.draftId ?? "default") === draft_id
              ? restoreDraft(selected, previous.revision + 1)
              : previous,
          );
        }
        if (explicitInput.current === target) explicitInput.current = undefined;
        current.ready = true;
        setStatus(
          head?.state === "saved" ? "实验输入已保存到应用数据。" : "Saving experiment input…",
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
      capturePendingInput(current);
      // An abandoned initial lookup owns no edits or pending write. Its late
      // response must neither hydrate another target nor block this window forever.
      if (
        !current.ready &&
        current.localInput === undefined &&
        !current.pending &&
        !current.failed &&
        !current.conflict &&
        !current.hydrating
      ) {
        if (unfinished.get(target) === current) unfinished.delete(target);
        if (session.current === current) session.current = undefined;
      }
    };
  }, [target, setDraft, reload, unfinished]);
  function queueInput(current: Session, input: string) {
    if (current.queued === input && current.confirmed === input) return;
    current.queued = input;
    if (current.failed || current.pending) return;
    current.pending = 1;
    if (mounted.current) setStatus("Saving experiment input…");
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
          const [workspace_id, experiment, draft_id] = JSON.parse(current.target) as [
            string,
            string,
            string,
          ];
          const command: Save = {
            operation_id: crypto.randomUUID(),
            expected_revision: current.revision,
            target: { workspace_id, experiment, draft_id },
            input: JSON.parse(sent) as Input,
            discard: false,
          };
          try {
            const result = await saveDraft(command);
            current.head = result.head ?? undefined;
            current.conflict = result.saved?.state === "conflict";
            if (!current.conflict) {
              current.revision = result.saved!.revision;
              current.confirmed = sent;
            }
            if (sent !== current.queued) continue;
            if (mounted.current && session.current === current)
              setStatus(
                current.conflict
                  ? "Another window changed this experiment. Your edits are retained as a separate conflict copy."
                  : "实验输入已保存到应用数据。",
              );
            break;
          } catch (error) {
            current.failed = command;
            if (mounted.current && session.current === current)
              setStatus(
                `Input not confirmed saved: ${String(error)}. Keep this window open and retry saving.`,
              );
          }
        }
      } finally {
        current.pending = 0;
        current.flushing = false;
        if (mounted.current) setEpoch((value) => value + 1);
      }
    })();
  }
  useEffect(() => {
    const current = session.current;
    if (current?.hydrating) {
      if (encoded !== current.hydrating) return;
      current.hydrating = undefined;
      setEpoch((value) => value + 1);
    }
    if (!encoded || !current?.ready || current.target !== target || current.queued === encoded)
      return;
    queueInput(current, encoded);
  }, [encoded, target, epoch]);
  const closeParticipant = useRef<{
    freeze: () => void;
    flush: () => Promise<"saved" | "blocked" | "unknown">;
    saved: () => boolean;
  }>({ freeze: () => {}, flush: async () => "unknown", saved: () => false });
  function currentInput() {
    if (!mounted.current && !session.current) return undefined;
    const latest = liveInput.current.close
      ? liveInput.current.close.getDraft()
      : liveInput.current.draft;
    return latest?.experiment && latest.definition && latest.workspaceId
      ? {
          target: JSON.stringify([
            latest.workspaceId,
            latest.experiment,
            latest.draftId ?? "default",
          ]),
          encoded: JSON.stringify(rawInput(latest)),
        }
      : undefined;
  }
  function allSaved() {
    const input = currentInput();
    if (
      input &&
      (!session.current ||
        session.current.target !== input.target ||
        session.current.confirmed !== input.encoded)
    )
      return false;
    return [...unfinished.values()].every(
      (item) =>
        item.ready &&
        !item.pending &&
        !item.failed &&
        !item.conflict &&
        !item.hydrating &&
        item.confirmed === item.queued,
    );
  }
  const participant = {
    freeze() {
      liveInput.current.close?.revoke();
      const input = currentInput();
      const active = session.current;
      if (input && active?.ready && !active.hydrating && active.target === input.target)
        queueInput(active, input.encoded);
      for (const item of unfinished.values()) {
        item.flushing = true;
        item.wake?.();
      }
    },
    async flush(): Promise<"saved" | "blocked" | "unknown"> {
      const input = currentInput();
      if (
        input &&
        (!session.current?.ready ||
          session.current.hydrating ||
          session.current.target !== input.target)
      )
        return "unknown";
      await Promise.all([...unfinished.values()].map((item) => item.queue));
      if ([...unfinished.values()].some((item) => item.failed || item.conflict)) return "blocked";
      return allSaved() ? "saved" : "unknown";
    },
    saved: allSaved,
  };
  useLayoutEffect(() => {
    closeParticipant.current = participant;
  });
  useEffect(
    () =>
      retainLaunchCloseParticipant(unfinished, {
        freeze: () => closeParticipant.current.freeze(),
        flush: () => closeParticipant.current.flush(),
        saved: () => closeParticipant.current.saved(),
      }),
    [unfinished],
  );
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
  useEffect(() => {
    const protectUnconfirmedInput = (event: BeforeUnloadEvent) => {
      if (
        ![...unfinished.values()].some(
          (item) => item.pending || item.failed || item.conflict || item.hydrating,
        )
      )
        return;
      // Browsers decide whether to show their native confirmation. This does not
      // establish native-host close protection or guarantee an asynchronous save.
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", protectUnconfirmedInput);
    return () => window.removeEventListener("beforeunload", protectUnconfirmedInput);
  }, [unfinished]);
  const current = visibleSession;
  return {
    // Only intentional input changes claim an unresolved initial read. Automatic
    // source/default observations must still allow the saved head to hydrate.
    retainPendingInput(input: LaunchDraft) {
      const active = session.current;
      if (
        !active ||
        active.ready ||
        !input.definition ||
        JSON.stringify([input.workspaceId, input.experiment, input.draftId ?? "default"]) !==
          active.target
      )
        return;
      active.localInput = JSON.stringify(rawInput(input));
    },
    status,
    canLeave: () =>
      !launchCloseBlocked() && !session.current?.failed && !session.current?.hydrating,
    unsavedTargets: [...unfinished.values()]
      .filter((item) => item.failed)
      .map((item) => JSON.parse(item.target) as [string, string, string]),
    keepExplicitInput(workspace: string, experiment: string, draftId = "default") {
      if (launchCloseBlocked()) return false;
      const selected = JSON.stringify([workspace, experiment, draftId]);
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
      if (launchCloseBlocked()) return false;
      const cached = unfinished.get(
        JSON.stringify([
          record.target.workspace_id,
          record.target.experiment,
          record.target.draft_id ?? "default",
        ]),
      );
      if (session.current?.pending || cached?.failed || cached?.pending || cached?.hydrating) {
        setStatus("Wait for the current input save before opening a historical copy.");
        return false;
      }
      requestedCopy.current = record;
      if (
        session.current?.target ===
        JSON.stringify([
          record.target.workspace_id,
          record.target.experiment,
          record.target.draft_id ?? "default",
        ])
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
      if (launchCloseBlocked()) return;
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
          if (mounted.current) setStatus(`Input not confirmed saved: ${String(error)}`);
        } finally {
          activeSession.pending = 0;
          if (mounted.current) setEpoch((value) => value + 1);
        }
      })();
      await activeSession.queue;
    },
    adoptLocal() {
      if (launchCloseBlocked()) return;
      const activeSession = session.current;
      if (!activeSession?.conflict || activeSession.pending) return;
      activeSession.revision = activeSession.head?.revision ?? 0;
      activeSession.conflict = false;
      activeSession.queued = "";
      setEpoch((value) => value + 1);
    },
  };
}
