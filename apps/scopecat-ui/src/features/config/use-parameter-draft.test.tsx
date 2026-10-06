// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  saveParameterDraft,
  freezeParameterDraft,
  commitParameterDraft,
  type ParameterDraftView,
} from "./parameter-draft-api";
import { useParameterDraft } from "./use-parameter-draft";
vi.mock("./parameter-draft-api", () => ({
  saveParameterDraft: vi.fn(),
  freezeParameterDraft: vi.fn(),
  commitParameterDraft: vi.fn(),
}));
const input = { name: "checkpoint", actor: "operator", branch: "", note: "", values: [] };
const initial: ParameterDraftView = {
  head_revision: 1,
  branch_changed: false,
  draft: {
    draft_id: "draft",
    revision: 1,
    working_branch: "",
    base: { revision_id: "base", content_hash: "sha256:base" },
    input,
    state: "saved",
    created_at: "2026-10-06T00:00:00Z",
  },
};
const receipt = (
  note: string,
  revision = 2,
  state: "saved" | "conflict" = "saved",
): ParameterDraftView => ({
  ...initial,
  head_revision: revision,
  draft: { ...initial.draft, state, revision, input: { ...input, note } },
});
beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  vi.mocked(saveParameterDraft).mockImplementation(async (_id, command) =>
    receipt(command.input.note, command.expected_revision + 1),
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
const mount = () => renderHook(() => useParameterDraft(initial));
it("flushes sustained typing by two seconds and flushes on navigation", async () => {
  const { result, unmount } = mount();
  for (let i = 0; i < 7; i++) {
    act(() => result.current.edit({ ...input, note: String(i) }));
    await act(() => vi.advanceTimersByTimeAsync(300));
  }
  expect(saveParameterDraft).toHaveBeenCalledTimes(1);
  act(() => result.current.edit({ ...input, note: "last" }));
  unmount();
  await act(async () => {});
  expect(vi.mocked(saveParameterDraft).mock.calls.at(-1)?.[1].input.note).toBe("last");
});
it("retains the latest input even when a delayed response reports a conflict", async () => {
  let finish!: (view: ParameterDraftView) => void;
  vi.mocked(saveParameterDraft).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { result } = mount();
  act(() => result.current.edit({ ...input, note: "first" }));
  await act(() => vi.advanceTimersByTimeAsync(500));
  act(() => result.current.edit({ ...input, note: "latest" }));
  vi.mocked(saveParameterDraft).mockImplementation(async () => receipt("latest", 3, "conflict"));
  await act(async () => finish(receipt("first", 2, "conflict")));
  expect(result.current.input.note).toBe("latest");
  expect(saveParameterDraft).toHaveBeenCalledTimes(2);
  expect(vi.mocked(saveParameterDraft).mock.calls[1]?.[1]).toMatchObject({
    expected_revision: 1,
    input: { note: "latest" },
  });
  await act(async () => {
    expect(await result.current.capture()).toBeUndefined();
  });
  expect(freezeParameterDraft).not.toHaveBeenCalled();
});
it("does not report failed saves as durable and retries before capture", async () => {
  vi.mocked(saveParameterDraft).mockRejectedValueOnce(new Error("offline"));
  vi.mocked(freezeParameterDraft).mockResolvedValue({
    draft_id: "draft",
    revision: 2,
    configuration: { kind: "parameters", ref: initial.draft.base, overrides: [] },
  });
  const { result } = mount();
  act(() => result.current.edit({ ...input, note: "1e" }));
  await act(() => vi.advanceTimersByTimeAsync(500));
  expect(result.current.status).toBe("failed");
  await act(async () => {
    await result.current.capture();
  });
  expect(freezeParameterDraft).toHaveBeenCalledWith("draft", 2);
  expect(commitParameterDraft).not.toHaveBeenCalled();
});
it("retries uncertain completion with the same saved revision", async () => {
  vi.mocked(commitParameterDraft).mockRejectedValueOnce(new Error("response lost"));
  vi.mocked(commitParameterDraft).mockResolvedValueOnce({
    ...initial,
    draft: { ...initial.draft, state: "completed" },
  });
  const { result } = mount();
  await act(async () => {
    await result.current.finish(false);
  });
  expect(result.current.status).toBe("failed");
  await act(async () => {
    await result.current.finish(false);
  });
  expect(vi.mocked(commitParameterDraft).mock.calls).toEqual([
    ["draft", 1],
    ["draft", 1],
  ]);
  expect(saveParameterDraft).not.toHaveBeenCalled();
});

it("protects browser unload and refuses editor departure until latest input is acknowledged", async () => {
  const { result } = mount();
  vi.mocked(saveParameterDraft).mockRejectedValueOnce(new Error("offline"));
  act(() => result.current.edit({ ...input, note: "unconfirmed" }));
  const leaving = new Event("beforeunload", { cancelable: true });
  await act(async () => {
    window.dispatchEvent(leaving);
  });
  expect(leaving.defaultPrevented).toBe(true);
  expect(result.current.status).toBe("failed");
  vi.mocked(saveParameterDraft).mockRejectedValueOnce(new Error("still offline"));
  await act(async () => {
    expect(await result.current.leave()).toBe(false);
  });
  expect(result.current.input.note).toBe("unconfirmed");
  act(() => result.current.edit({ ...input, note: "latest" }));
  await act(async () => {
    expect(await result.current.leave()).toBe(true);
  });
  expect(vi.mocked(saveParameterDraft).mock.calls.at(-1)?.[1].input.note).toBe("latest");
  const saved = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(saved);
  expect(saved.defaultPrevented).toBe(false);
});

it("permits leaving once a conflicting copy is durably retained", async () => {
  vi.mocked(saveParameterDraft).mockResolvedValue(receipt("conflict", 2, "conflict"));
  const { result } = mount();
  act(() => result.current.edit({ ...input, note: "conflict" }));
  await act(async () => {
    expect(await result.current.leave()).toBe(true);
  });
  expect(result.current.status).toBe("conflict");
});
