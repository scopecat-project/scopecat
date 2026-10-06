import { useEffect, useRef, useState } from "react";
import { errorMessage } from "../../lib/presentation";
import {
  saveDecisionDraft,
  type DecisionDraftInput,
  type DecisionDraftTarget,
  type DecisionDraftBaseline,
  type DecisionDraftView,
} from "./decision-api";

type Status = "saved" | "unsaved" | "saving" | "conflict" | "failed";

// A single mounted editor owns its queue. Pending writes outlive navigation;
// only acknowledged app-database commits are described as saved.
export function useDecisionDraft(
  target: DecisionDraftTarget,
  baseline: DecisionDraftBaseline,
  initial: DecisionDraftView,
  defaults: DecisionDraftInput,
) {
  const [input, setInput] = useState(
    initial.draft?.state === "saved" ? initial.draft.input : defaults,
  );
  const [status, setStatus] = useState<Status>("saved");
  const [discardFailed, setDiscardFailed] = useState(false);
  const [discarding, setDiscarding] = useState(false);
  const [error, setError] = useState<string>();
  const [validity, setValidity] = useState(initial.validity);
  const [started, setStarted] = useState(initial.draft?.state === "saved");
  const state = useRef({
    input,
    baseline: initial.draft?.state === "saved" ? initial.draft.baseline : baseline,
    revision: initial.head_revision,
    head: initial.head_revision,
    generation: 0,
    acknowledged: 0,
    alive: true,
    pending: undefined as Promise<boolean> | undefined,
    timer: undefined as ReturnType<typeof setTimeout> | undefined,
    maxTimer: undefined as ReturnType<typeof setTimeout> | undefined,
    discarding: false,
    conflict: false,
    valid: initial.validity === "current",
    latestBaseline: baseline,
  });
  const current = state.current;
  current.latestBaseline = baseline;
  const notify = (next: Status, message?: string) => {
    if (current.alive) {
      setStatus(next);
      setError(message);
    }
  };
  const clearTimers = () => {
    clearTimeout(current.timer);
    clearTimeout(current.maxTimer);
    current.timer = undefined;
    current.maxTimer = undefined;
  };
  const flush = async (): Promise<boolean> => {
    clearTimers();
    if (current.discarding) return false;
    if (current.pending) {
      const ok = await current.pending;
      return current.generation !== current.acknowledged && ok ? flush() : ok;
    }
    if (current.generation === current.acknowledged) return !current.conflict && current.valid;
    if (current.alive) setDiscardFailed(false);
    const generation = current.generation;
    notify("saving");
    const pending = saveDecisionDraft({
      target,
      baseline: current.baseline,
      expected_revision: current.revision,
      input: current.input,
      discard: false,
    })
      .then((view) => {
        current.valid = view.validity === "current";
        current.head = view.head_revision;
        current.conflict = view.draft?.state === "conflict";
        // Never replace input from a response to an earlier edit.
        if (!current.conflict) current.revision = view.head_revision;
        current.acknowledged = generation;
        if (current.alive) {
          setValidity(view.validity);
          setStarted(true);
        }
        notify(
          current.generation !== generation ? "unsaved" : current.conflict ? "conflict" : "saved",
        );
        return !current.conflict && current.valid;
      })
      .catch((failure: unknown) => {
        notify("failed", errorMessage(failure));
        return false;
      })
      .finally(() => {
        current.pending = undefined;
      });
    current.pending = pending;
    const ok = await pending;
    return current.generation > generation ? flush() : ok;
  };
  const edit = (next: DecisionDraftInput) => {
    current.input = next;
    current.generation += 1;
    setInput(next);
    notify("unsaved");
    clearTimeout(current.timer);
    current.timer = setTimeout(() => {
      void flush();
    }, 500);
    current.maxTimer ??= setTimeout(() => {
      void flush();
    }, 2000);
  };
  useEffect(() => {
    current.alive = true;
    const leave = () => {
      void flush();
    };
    const hide = () => {
      if (document.visibilityState === "hidden") leave();
    };
    window.addEventListener("pagehide", leave);
    document.addEventListener("visibilitychange", hide);
    return () => {
      current.alive = false;
      window.removeEventListener("pagehide", leave);
      document.removeEventListener("visibilitychange", hide);
      void flush();
    };
    // This queue belongs to one stable logical Decision identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const discard = async () => {
    if (current.discarding) return;
    current.discarding = true;
    setDiscarding(true);
    setDiscardFailed(false);
    clearTimers();
    if (current.pending) await current.pending;
    notify("saving");
    try {
      const view = await saveDecisionDraft({
        target,
        baseline: current.baseline,
        expected_revision: current.revision,
        input: current.input,
        discard: true,
      });
      current.head = view.head_revision;
      if (view.draft?.state === "conflict") {
        current.conflict = true;
        notify("conflict");
        return;
      }
      current.revision = view.head_revision;
      current.baseline = baseline;
      current.acknowledged = current.generation;
      current.input = defaults;
      current.conflict = false;
      current.valid = view.validity === "current";
      if (current.alive) {
        setInput(defaults);
        setValidity(view.validity);
        setStarted(false);
      }
      notify("saved");
    } catch (failure) {
      if (current.alive) setDiscardFailed(true);
      notify("failed", errorMessage(failure));
    } finally {
      current.discarding = false;
      if (current.alive) setDiscarding(false);
    }
  };
  const resolve = async () => {
    if (current.pending) await current.pending;
    current.revision = current.head;
    current.conflict = false;
    current.generation += 1;
    return flush();
  };
  const changed =
    current.baseline.run_revision !== baseline.run_revision ||
    current.baseline.step_revision !== baseline.step_revision ||
    current.baseline.request_hash !== baseline.request_hash;
  return {
    input,
    edit,
    status,
    error,
    started,
    flush,
    discard,
    resolve,
    discarding,
    discardFailed,
    isCurrent: (snapshot: DecisionDraftInput) =>
      current.input === snapshot &&
      current.valid &&
      current.baseline.run_revision === current.latestBaseline.run_revision &&
      current.baseline.step_revision === current.latestBaseline.step_revision &&
      current.baseline.request_hash === current.latestBaseline.request_hash,
    invalid: changed || validity !== "current",
  };
}
