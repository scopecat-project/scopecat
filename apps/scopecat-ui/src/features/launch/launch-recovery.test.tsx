// @vitest-environment jsdom
import { useState } from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useLaunchRecovery, restoreDraft, type DraftRecord } from "./launch-recovery";
import type { LaunchDraft } from "./LaunchDraft";
import type { LaunchCatalogEntry } from "./launch-api";
import { defaultSelection } from "./scientific-selection";

const entry: LaunchCatalogEntry = {
  id: "signal",
  version: "1",
  title: "Signal",
  description: "",
  actions: ["preview", "submit"],
  kind: "diagnostic",
  configuration_effect: "none",
  review: null,
  request: { properties: { center: { type: "number", default: 0 } }, required: [] },
  controls: [],
};
const initial = (): LaunchDraft => ({
  workspaceId: "author",
  experiment: "signal",
  definition: JSON.stringify(entry),
  controlDefinition: "",
  selection: defaultSelection(),
  values: { center: "0" },
  controls: { position: { mode: "range", start: "1", stop: "2", points: "3", unit: null } },
  actor: "operator",
  pending: false,
  error: "",
  notice: "",
  revision: 1,
});
let history: DraftRecord[];
let failSave: boolean;
let holdSave: Promise<void> | undefined;
let commands: string[];
beforeEach(() => {
  history = [];
  commands = [];
  failSave = false;
  holdSave = undefined;
  const operations = new Map<string, DraftRecord>();
  vi.stubGlobal("fetch", async (request: Request) => {
    const path = new URL(request.url).pathname;
    const body = await request.json();
    const sameTarget = (item: DraftRecord) =>
      JSON.stringify(item.target) === JSON.stringify(path.endsWith("/read") ? body : body.target);
    if (path.endsWith("/read"))
      return Response.json({
        head:
          history.filter((item) => item.state !== "conflict" && sameTarget(item)).at(-1) ?? null,
      });
    commands.push(body.operation_id);
    await holdSave;
    if (failSave) throw new Error("offline");
    const head = history.filter((item) => item.state !== "conflict" && sameTarget(item)).at(-1);
    const replay = operations.get(body.operation_id);
    if (replay) return Response.json({ saved: replay, head });
    const saved = {
      ...body,
      state: body.expected_revision === (head?.revision ?? 0) ? "saved" : "conflict",
      revision: history.length + 1,
      created_at: new Date().toISOString(),
    };
    operations.set(body.operation_id, saved);
    history.push(saved);
    return Response.json({ saved, head: saved.state === "saved" ? saved : head });
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function mount() {
  return renderHook(() => {
    const [draft, setDraft] = useState<LaunchDraft | undefined>(initial);
    return { draft: draft!, setDraft, recovery: useLaunchRecovery(draft, setDraft) };
  });
}
const saved = async (view: ReturnType<typeof mount>) =>
  waitFor(() =>
    expect(view.result.current.recovery.status).toBe("Experiment input saved in application data."),
  );
it("restores invalid raw strings and input modes after the provider is destroyed", async () => {
  const first = mount();
  await saved(first);
  act(() =>
    first.result.current.setDraft((current) => ({
      ...current!,
      values: { center: "-", orphan: "keep" },
      controls: { position: { mode: "values", values: "1, nope, 3", unit: "V" } },
    })),
  );
  await saved(first);
  first.unmount();
  const reopened = mount();
  await saved(reopened);
  expect(reopened.result.current.draft.values).toEqual({ center: "-", orphan: "keep" });
  expect(reopened.result.current.draft.controls.position).toMatchObject({
    mode: "values",
    values: "1, nope, 3",
    unit: "V",
  });
  expect(reopened.result.current.draft.needsReview).toBe(true);
  expect(reopened.result.current.draft.preview).toBeUndefined();
});
it("retains both windows and fences submission until explicit conflict choice", async () => {
  const one = mount();
  await saved(one);
  const two = mount();
  await saved(two);
  // Both windows start at the same revision; the later conditional write conflicts.
  act(() =>
    one.result.current.setDraft((current) => ({ ...current!, values: { center: "window one" } })),
  );
  await saved(one);
  act(() =>
    two.result.current.setDraft((current) => ({ ...current!, values: { center: "window two" } })),
  );
  await waitFor(() => expect(two.result.current.recovery.conflict).toBe(true));
  expect(history.at(-1)?.state).toBe("conflict");
  await expect(two.result.current.recovery.flush()).rejects.toThrow("Resolve and save");
  expect(one.result.current.draft.values.center).toBe("window one");
  act(() => two.result.current.recovery.adoptLocal());
  await saved(two);
  expect(history.at(-1)?.input.values.center).toBe("window two");
  expect(history.some((item) => item.state === "conflict")).toBe(true);
});
it("waits for the queued save and retries the identical operation after a lost save response", async () => {
  const view = mount();
  await saved(view);
  let release!: () => void;
  holdSave = new Promise<void>((resolve) => {
    release = resolve;
  });
  act(() =>
    view.result.current.setDraft((current) => ({ ...current!, values: { center: "new" } })),
  );
  let finished = false;
  const flushing = view.result.current.recovery.flush().then(() => {
    finished = true;
  });
  await act(async () => {
    await Promise.resolve();
  });
  expect(finished).toBe(false);
  await act(async () => {
    release();
    await flushing;
  });
  failSave = true;
  act(() =>
    view.result.current.setDraft((current) => ({ ...current!, values: { center: "newer" } })),
  );
  await waitFor(() => expect(view.result.current.recovery.status).toContain("not confirmed saved"));
  const operation = commands.at(-1);
  failSave = false;
  await act(async () => {
    await view.result.current.recovery.retry();
  });
  await saved(view);
  expect(commands.filter((item) => item === operation)).toHaveLength(2);
  expect(history.at(-1)?.input.values.center).toBe("newer");
});

it("conditions a historical copy against its target head even when opened from another experiment", async () => {
  const view = mount();
  await saved(view);
  const old = history[0]!;
  act(() =>
    view.result.current.setDraft((current) => ({ ...current!, values: { center: "new head" } })),
  );
  await saved(view);
  act(() =>
    view.result.current.setDraft((current) => ({
      ...current!,
      experiment: "other",
      definition: JSON.stringify({ ...entry, id: "other" }),
    })),
  );
  await saved(view);
  act(() => {
    view.result.current.recovery.selectCopy(old);
    view.result.current.setDraft(restoreDraft(old, 10));
  });
  await waitFor(() => expect(view.result.current.recovery.conflict).toBe(true));
  await expect(view.result.current.recovery.flush()).rejects.toThrow("Resolve and save");
  expect(
    history.filter((item) => item.target.experiment === "signal" && item.state === "saved").at(-1)
      ?.input.values.center,
  ).toBe("new head");
});

it("restores a pending target's local copy when its save fails after navigation", async () => {
  const view = mount();
  await saved(view);
  let release!: () => void;
  holdSave = new Promise<void>((resolve) => {
    release = resolve;
  });
  act(() =>
    view.result.current.setDraft((current) => ({
      ...current!,
      values: { center: "pending original" },
    })),
  );
  await waitFor(() => expect(view.result.current.recovery.writing).toBe(true));
  act(() =>
    view.result.current.setDraft((current) => ({
      ...current!,
      experiment: "other",
      definition: JSON.stringify({ ...entry, id: "other" }),
      values: { center: "other input" },
    })),
  );
  failSave = true;
  await act(async () => {
    release();
  });
  await waitFor(() =>
    expect(view.result.current.recovery.unsavedTargets).toContainEqual(["author", "signal"]),
  );
  act(() => view.result.current.setDraft(initial()));
  await waitFor(() => expect(view.result.current.draft.values.center).toBe("pending original"));
  expect(view.result.current.recovery.status).toContain("not confirmed saved");
  failSave = false;
  await act(async () => {
    await view.result.current.recovery.retry();
  });
  await saved(view);
  expect(
    history.filter((item) => item.target.experiment === "signal").at(-1)?.input.values.center,
  ).toBe("pending original");
});

it("does not queue transitional defaults when returning before a pending save finishes", async () => {
  const view = mount();
  await saved(view);
  let release!: () => void;
  holdSave = new Promise<void>((resolve) => {
    release = resolve;
  });
  act(() =>
    view.result.current.setDraft((current) => ({
      ...current!,
      values: { center: "retained pending" },
    })),
  );
  await waitFor(() => expect(view.result.current.recovery.writing).toBe(true));
  act(() =>
    view.result.current.setDraft({
      ...initial(),
      experiment: "other",
      definition: JSON.stringify({ ...entry, id: "other" }),
    }),
  );
  act(() => view.result.current.setDraft({ ...initial(), revision: 25 }));
  await waitFor(() => expect(view.result.current.draft.values.center).toBe("retained pending"));
  expect(view.result.current.draft.revision).toBe(26);
  await act(async () => {
    release();
  });
  await saved(view);
  expect(
    history
      .filter((item) => item.target.experiment === "signal")
      .map((item) => item.input.values.center),
  ).toEqual(["0", "retained pending"]);
});

it("rejects history, plan, and handoff replacement of a different target's failed local copy", async () => {
  const view = mount();
  await saved(view);
  const old = history[0]!;
  let release!: () => void;
  holdSave = new Promise<void>((resolve) => {
    release = resolve;
  });
  act(() =>
    view.result.current.setDraft((current) => ({
      ...current!,
      values: { center: "unsaved original" },
    })),
  );
  await waitFor(() => expect(view.result.current.recovery.writing).toBe(true));
  act(() =>
    view.result.current.setDraft({
      ...initial(),
      experiment: "other",
      definition: JSON.stringify({ ...entry, id: "other" }),
    }),
  );
  failSave = true;
  await act(async () => {
    release();
  });
  await waitFor(() =>
    expect(view.result.current.recovery.unsavedTargets).toContainEqual(["author", "signal"]),
  );
  act(() => {
    expect(view.result.current.recovery.selectCopy(old)).toBe(false);
    expect(view.result.current.recovery.keepExplicitInput("author", "signal")).toBe(false);
  });
  expect(view.result.current.recovery.unsavedTargets).toContainEqual(["author", "signal"]);
  act(() => view.result.current.setDraft(initial()));
  await waitFor(() => expect(view.result.current.draft.values.center).toBe("unsaved original"));
});
