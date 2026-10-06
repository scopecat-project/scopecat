import { useEffect, useRef, useState } from "react";
import { errorMessage } from "../../lib/presentation";
import {
  freezeParameterDraft,
  commitParameterDraft,
  saveParameterDraft,
  type ParameterDraftInput,
  type ParameterDraftView,
} from "./parameter-draft-api";

// One queue per explicit parameter editing identity. Acknowledgements never replace newer input.
export function useParameterDraft(initial: ParameterDraftView) {
  const [input, setInput] = useState(initial.draft.input);
  const [status, setStatus] = useState("saved");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const state = useRef({
    input,
    revision: initial.head_revision,
    generation: 0,
    ack: 0,
    conflict: initial.draft.state !== "saved",
    closed: initial.draft.state !== "saved",
    alive: true,
    pending: undefined as Promise<boolean> | undefined,
    timer: undefined as ReturnType<typeof setTimeout> | undefined,
    max: undefined as ReturnType<typeof setTimeout> | undefined,
    busy: false,
  });
  const s = state.current;
  const notify = (next: string, failure?: unknown) => {
    if (s.alive) {
      setStatus(next);
      setError(failure ? errorMessage(failure) : undefined);
    }
  };
  const clear = () => {
    clearTimeout(s.timer);
    clearTimeout(s.max);
    s.timer = s.max = undefined;
  };
  const flush = async (): Promise<boolean> => {
    clear();
    if (s.pending) {
      const ok = await s.pending;
      return (ok || s.conflict) && s.generation !== s.ack ? flush() : ok;
    }
    if (s.closed) return false;
    if (s.generation === s.ack) return !s.conflict;
    const generation = s.generation;
    notify("saving");
    const pending = saveParameterDraft(initial.draft.draft_id, {
      expected_revision: s.revision,
      input: s.input,
      discard: false,
    })
      .then((view) => {
        s.conflict = view.draft.state === "conflict";
        if (!s.conflict) s.revision = view.head_revision;
        s.ack = generation;
        notify(s.generation !== generation ? "unsaved" : s.conflict ? "conflict" : "saved");
        return !s.conflict;
      })
      .catch((failure: unknown) => {
        notify("failed", failure);
        return false;
      })
      .finally(() => {
        s.pending = undefined;
      });
    s.pending = pending;
    const ok = await pending;
    return s.generation > generation && (ok || s.conflict) ? flush() : ok;
  };
  const edit = (next: ParameterDraftInput) => {
    if (s.busy || s.closed) return;
    s.input = next;
    s.generation++;
    setInput(next);
    notify("unsaved");
    clearTimeout(s.timer);
    s.timer = setTimeout(() => void flush(), 500);
    s.max ??= setTimeout(() => void flush(), 2000);
  };
  useEffect(() => {
    s.alive = true;
    const leave = () => void flush();
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (s.generation !== s.ack) {
        event.preventDefault();
        void flush();
      }
    };
    const hide = () => {
      if (document.visibilityState === "hidden") leave();
    };
    window.addEventListener("beforeunload", beforeUnload);
    window.addEventListener("pagehide", leave);
    document.addEventListener("visibilitychange", hide);
    return () => {
      s.alive = false;
      window.removeEventListener("beforeunload", beforeUnload);
      window.removeEventListener("pagehide", leave);
      document.removeEventListener("visibilitychange", hide);
      void flush();
    };
    // The mounted editor is keyed by draft ID, not the immutable baseline.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const leave = async (): Promise<boolean> => {
    if (s.busy) return false;
    s.busy = true;
    setBusy(true);
    try {
      await flush();
      // A retained conflict is durable too. Failure keeps the editor mounted.
      return s.generation === s.ack;
    } finally {
      s.busy = false;
      if (s.alive) setBusy(false);
    }
  };
  const finish = async (discard: boolean): Promise<ParameterDraftView | undefined> => {
    if (s.busy) return;
    s.busy = true;
    setBusy(true);
    try {
      if (!(await flush())) return;
      const view = discard
        ? await saveParameterDraft(initial.draft.draft_id, {
            expected_revision: s.revision,
            input: s.input,
            discard: true,
          })
        : await commitParameterDraft(initial.draft.draft_id, s.revision);
      if (view.draft.state === "conflict") {
        s.conflict = true;
        notify("conflict");
        return;
      }
      s.closed = true;
      clear();
      return view;
    } catch (failure) {
      notify("failed", failure);
    } finally {
      s.busy = false;
      if (s.alive) setBusy(false);
    }
    return undefined;
  };
  const capture = async () => {
    if (s.busy || s.closed) return;
    s.busy = true;
    setBusy(true);
    try {
      if (!(await flush())) return;
      return await freezeParameterDraft(initial.draft.draft_id, s.revision);
    } catch (failure) {
      notify("failed", failure);
    } finally {
      s.busy = false;
      if (s.alive) setBusy(false);
    }
    return undefined;
  };
  const resolve = async (reviewed: ParameterDraftView) => {
    if (s.busy || reviewed.draft.state !== "saved") return;
    if (s.pending) await s.pending;
    s.revision = reviewed.head_revision;
    s.conflict = false;
    s.generation++;
    return flush();
  };
  return {
    input,
    edit,
    flush,
    leave,
    finish,
    capture,
    resolve,
    status,
    error,
    busy,
    conflict: s.conflict,
  };
}
