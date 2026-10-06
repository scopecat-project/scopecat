// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { saveDecisionDraft, type DecisionDraftSave, type DecisionDraftView } from "./decision-api";
import { useDecisionDraft } from "./use-decision-draft";
vi.mock("./decision-api", () => ({ saveDecisionDraft: vi.fn() }));
const target = { procedure_run_id: "p", step_key: "peak", attempt: 1 };
const baseline = { run_revision: 4, step_revision: 2, request_hash: "hash" };
const input = {
  actor: "",
  actor_kind: "human" as const,
  note: "",
  value_text: "{}",
  use_json: true,
};
const initial: DecisionDraftView = { draft: null, head_revision: 0, validity: "current" };
function receipt(
  command: DecisionDraftSave,
  revision = 1,
  state: "saved" | "conflict" | "discarded" = "saved",
): DecisionDraftView {
  return {
    draft: {
      target,
      baseline: command.baseline,
      input: command.input,
      revision,
      state,
      created_at: "2026-10-06T00:00:00Z",
    },
    head_revision: revision,
    validity: "current",
  };
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  vi.mocked(saveDecisionDraft).mockImplementation(async (command) => receipt(command));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
const mount = () => renderHook(() => useDecisionDraft(target, baseline, initial, input));
it("uses a maximum wait during uninterrupted typing", async () => {
  const { result } = mount();
  for (let i = 0; i < 7; i++) {
    act(() => result.current.edit({ ...input, note: String(i) }));
    await act(() => vi.advanceTimersByTimeAsync(300));
  }
  expect(saveDecisionDraft).toHaveBeenCalledTimes(1);
  expect(vi.mocked(saveDecisionDraft).mock.calls[0]![0].input.note).toBe("6");
  expect(result.current.status).toBe("saved");
});
it("serializes writes without old responses replacing newer edits", async () => {
  let finish!: (view: DecisionDraftView) => void;
  vi.mocked(saveDecisionDraft).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { result } = mount();
  act(() => result.current.edit({ ...input, note: "first" }));
  await act(() => vi.advanceTimersByTimeAsync(500));
  act(() => result.current.edit({ ...input, note: "second" }));
  expect(result.current.status).toBe("unsaved");
  await act(async () => finish(receipt(vi.mocked(saveDecisionDraft).mock.calls[0]![0])));
  expect(result.current.input.note).toBe("second");
  expect(saveDecisionDraft).toHaveBeenCalledTimes(2);
  expect(vi.mocked(saveDecisionDraft).mock.calls[1]![0]).toMatchObject({
    expected_revision: 1,
    input: { note: "second" },
  });
});
it("keeps a failed write unsaved and explicitly retries", async () => {
  vi.mocked(saveDecisionDraft).mockRejectedValueOnce(new Error("offline"));
  const { result } = mount();
  act(() => result.current.edit({ ...input, value_text: "{unfinished" }));
  await act(() => vi.advanceTimersByTimeAsync(500));
  expect(result.current.status).toBe("failed");
  expect(result.current.input.value_text).toBe("{unfinished");
  await act(async () => {
    await result.current.flush();
  });
  expect(result.current.status).toBe("saved");
});
it("requires an explicit CAS conflict resolution", async () => {
  vi.mocked(saveDecisionDraft).mockImplementationOnce(async (command) =>
    receipt(command, 2, "conflict"),
  );
  const { result } = mount();
  act(() => result.current.edit({ ...input, note: "local" }));
  await act(() => vi.advanceTimersByTimeAsync(500));
  expect(result.current.status).toBe("conflict");
  expect(result.current.input.note).toBe("local");
  await act(async () => {
    expect(await result.current.flush()).toBe(false);
  });
  expect(saveDecisionDraft).toHaveBeenCalledTimes(1);
  await act(async () => {
    await result.current.resolve();
  });
  expect(vi.mocked(saveDecisionDraft).mock.calls[1]![0].expected_revision).toBe(2);
});
it("flushes latest edits on navigation", () => {
  const { result, unmount } = mount();
  act(() => result.current.edit({ ...input, note: "leaving" }));
  unmount();
  expect(saveDecisionDraft).toHaveBeenCalledWith(
    expect.objectContaining({ input: expect.objectContaining({ note: "leaving" }) }),
  );
});
it("retains the original baseline and invalidates recording", async () => {
  const { result, rerender } = renderHook(
    ({ revision }) =>
      useDecisionDraft(target, { ...baseline, step_revision: revision }, initial, input),
    { initialProps: { revision: 2 } },
  );
  act(() => result.current.edit({ ...input, note: "keep me" }));
  rerender({ revision: 3 });
  expect(result.current.invalid).toBe(true);
  await act(() => vi.advanceTimersByTimeAsync(500));
  expect(vi.mocked(saveDecisionDraft).mock.calls[0]![0].baseline.step_revision).toBe(2);
  expect(result.current.isCurrent(result.current.input)).toBe(false);
});
it("serializes duplicate discard and does not save on unmount during discard", async () => {
  let finish!: (view: DecisionDraftView) => void;
  vi.mocked(saveDecisionDraft).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { result, unmount } = mount();
  act(() => result.current.edit({ ...input, note: "retained discard" }));
  act(() => {
    void result.current.discard();
    void result.current.discard();
  });
  unmount();
  expect(saveDecisionDraft).toHaveBeenCalledTimes(1);
  await act(async () =>
    finish(receipt(vi.mocked(saveDecisionDraft).mock.calls[0]![0], 1, "discarded")),
  );
  expect(saveDecisionDraft).toHaveBeenCalledTimes(1);
});
it("discard never revives a completed request", async () => {
  vi.mocked(saveDecisionDraft).mockImplementationOnce(async (command) => ({
    ...receipt(command, 1, "discarded"),
    validity: "no_longer_waiting",
  }));
  const { result } = mount();
  await act(async () => {
    await result.current.discard();
  });
  expect(result.current.invalid).toBe(true);
  expect(result.current.isCurrent(result.current.input)).toBe(false);
});
it("reopens a discarded head as fresh unsaved defaults, not a saved draft", () => {
  const command = { target, baseline, input, expected_revision: 0, discard: true };
  const { result } = renderHook(() =>
    useDecisionDraft(target, baseline, receipt(command, 1, "discarded"), input),
  );
  expect(result.current.started).toBe(false);
});
it("preserves input after discard failure and retries discard explicitly", async () => {
  vi.mocked(saveDecisionDraft).mockRejectedValueOnce(new Error("offline"));
  const { result } = mount();
  act(() => result.current.edit({ ...input, note: "retain this" }));
  await act(async () => {
    await result.current.discard();
  });
  expect(result.current.discardFailed).toBe(true);
  expect(result.current.input.note).toBe("retain this");
  await act(async () => {
    await result.current.discard();
  });
  expect(vi.mocked(saveDecisionDraft).mock.calls[1]![0].discard).toBe(true);
  expect(result.current.discardFailed).toBe(false);
});
