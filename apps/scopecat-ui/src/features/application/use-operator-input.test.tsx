// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { readOperatorSettings } from "./OperatorSettings";
import { useOperatorInput } from "./use-operator-input";

vi.mock("./OperatorSettings", () => ({ readOperatorSettings: vi.fn() }));
beforeEach(() => vi.resetAllMocks());
it("waits for a server snapshot and keeps it for this form", async () => {
  vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 1, operator: "Alice" });
  const { result, rerender } = renderHook(() => useOperatorInput());
  expect(result.current.ready).toBe(false);
  await waitFor(() => expect(result.current.actor).toBe("Alice"));
  vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 2, operator: "Bob" });
  rerender();
  expect(await result.current.snapshot()).toBe("Alice");
  expect(readOperatorSettings).toHaveBeenCalledTimes(1);
});
it("never replaces an explicit edit with a late default", async () => {
  let resolve!: (value: { revision: number; operator: string }) => void;
  vi.mocked(readOperatorSettings).mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const { result } = renderHook(() => useOperatorInput());
  act(() => result.current.setActor(""));
  await act(async () => resolve({ revision: 1, operator: "Alice" }));
  expect(result.current.actor).toBe("");
  expect(await result.current.snapshot()).toBe("");
});
it("preserves recovered attribution including an empty value without reading Settings", () => {
  const { result } = renderHook(() => useOperatorInput(""));
  expect(result.current.ready).toBe(true);
  expect(result.current.actor).toBe("");
  expect(readOperatorSettings).not.toHaveBeenCalled();
});
it("does not invent attribution when Settings cannot be read; explicit edit can proceed", async () => {
  vi.mocked(readOperatorSettings).mockRejectedValue(new Error("Settings unavailable"));
  const { result } = renderHook(() => useOperatorInput());
  await waitFor(() => expect(result.current.error).toBe("Settings unavailable"));
  expect(result.current.ready).toBe(false);
  act(() => result.current.setActor("Manual reviewer"));
  expect(await result.current.snapshot()).toBe("Manual reviewer");
});

it("reads fresh defaults for each new operation while keeping explicit overrides", async () => {
  vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 1, operator: "Alice" });
  const { result } = renderHook(() => useOperatorInput());
  await waitFor(() => expect(result.current.actor).toBe("Alice"));
  vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 2, operator: "Bob" });
  await act(async () => expect(await result.current.freshSnapshot()).toBe("Bob"));
  act(() => result.current.setActor("Alice"));
  expect(result.current.overridden).toBe(true);
  expect(await result.current.freshSnapshot()).toBe("Alice");
});

it("stops command preparation when an explicit edit changes intent during its default lookup", async () => {
  vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 1, operator: "Alice" });
  const { result } = renderHook(() => useOperatorInput());
  await waitFor(() => expect(result.current.actor).toBe("Alice"));
  let release!: (value: { revision: number; operator: string }) => void;
  vi.mocked(readOperatorSettings).mockImplementation(
    () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  );
  const prepared = result.current.freshSnapshot().catch((error: unknown) => error);
  act(() => result.current.setActor("Bob"));
  await act(async () => {
    release({ revision: 2, operator: "Carol" });
    expect(await prepared).toBeInstanceOf(Error);
    expect(String(await prepared)).toContain("Operator changed");
  });
  expect(await result.current.freshSnapshot()).toBe("Bob");
});
