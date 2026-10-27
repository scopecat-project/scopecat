// @vitest-environment jsdom

import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { measurementEventRefresh } from "./measurement-event-refresh";

const cleanups: (() => void)[] = [];
afterEach(() => {
  for (const cleanup of cleanups.splice(0)) cleanup();
});

function fixture() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  const refresh = measurementEventRefresh(client);
  cleanups.push(() => {
    refresh.dispose();
    client.clear();
  });
  function observe(key: readonly string[], options: { mount?: boolean; poll?: boolean } = {}) {
    const reads: {
      signal: AbortSignal;
      resolve: (value: number) => void;
      reject: (error: Error) => void;
    }[] = [];
    if (!options.mount) client.setQueryData(key, 0);
    const observer = new QueryObserver(client, {
      queryKey: key,
      staleTime: Infinity,
      refetchInterval: options.poll ? 10 : false,
      refetchIntervalInBackground: true,
      queryFn: ({ signal }) =>
        new Promise<number>((resolve, reject) => {
          reads.push({ signal, resolve, reject });
          signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    });
    const stop = observer.subscribe(() => {});
    cleanups.push(stop);
    return {
      reads,
      stop,
      key,
      stopPolling: () => observer.setOptions({ ...observer.options, refetchInterval: false }),
    };
  }
  return { client, refresh, observe };
}

it("coalesces notifications independently for each exact active query", async () => {
  const { client, refresh, observe } = fixture();
  const slice = observe(["measurement-slice", "run", "slice-a"]);
  const trace = observe(["measurement-trace", "run", "trace-a"]);
  refresh.invalidate("run");
  await vi.waitFor(() => expect(slice.reads).toHaveLength(1));
  for (let i = 0; i < 20; i += 1) refresh.invalidate("run");
  expect(slice.reads[0]!.signal.aborted).toBe(false);
  expect(trace.reads[0]!.signal.aborted).toBe(false);
  slice.reads[0]!.resolve(1);
  await vi.waitFor(() => expect(slice.reads).toHaveLength(2));
  expect(trace.reads).toHaveLength(1);
  expect(client.getQueryData(slice.key)).toBe(1);
  slice.reads[1]!.resolve(2);
  trace.reads[0]!.resolve(3);
  await vi.waitFor(() => expect(trace.reads).toHaveLength(2));
  trace.reads[1]!.resolve(4);
  await vi.waitFor(() => expect(client.isFetching()).toBe(0));
  expect(slice.reads).toHaveLength(2);
  expect(client.getQueryData(slice.key)).toBe(2);
  expect(client.getQueryData(trace.key)).toBe(4);
});

it("finishes a queued retry after an error and stops when no notification remains", async () => {
  const { client, refresh, observe } = fixture();
  const query = observe(["measurement-slice", "run", "slice"]);
  refresh.invalidate("run");
  refresh.invalidate("run");
  query.reads[0]!.reject(new Error("first read failed"));
  await vi.waitFor(() => expect(query.reads).toHaveLength(2));
  query.reads[1]!.reject(new Error("trailing read failed"));
  await vi.waitFor(() => expect(client.isFetching()).toBe(0));
  expect(client.getQueryState(query.key)?.error?.message).toBe("trailing read failed");
  expect(query.reads).toHaveLength(2);
  refresh.invalidate("run");
  await vi.waitFor(() => expect(query.reads).toHaveLength(3));
  query.reads[2]!.resolve(3);
  await vi.waitFor(() => expect(client.getQueryData(query.key)).toBe(3));
});

it("keeps identity-change aborts and never transfers a late value to another query", async () => {
  const { client, refresh, observe } = fixture();
  const old = observe(["measurement-slice", "run-a", "slice-a"]);
  refresh.invalidate("run-a");
  refresh.invalidate("run-a");
  old.stop();
  expect(old.reads[0]!.signal.aborted).toBe(true);
  const current = observe(["measurement-slice", "run-b", "slice-b"]);
  refresh.invalidate("run-b");
  current.reads[0]!.resolve(7);
  old.reads[0]!.resolve(99);
  await vi.waitFor(() => expect(client.isFetching()).toBe(0));
  expect(old.reads).toHaveLength(1);
  expect(client.getQueryData(current.key)).toBe(7);
  expect(client.getQueryData(old.key)).toBe(0);
});

it("discards pending work on unmount while normal observer cancellation remains intact", async () => {
  const { client, refresh, observe } = fixture();
  const query = observe(["measurement-trace", "run", "trace"]);
  refresh.invalidate("run");
  refresh.invalidate("run");
  query.stop();
  refresh.dispose();
  refresh.invalidate("run");
  await vi.waitFor(() => expect(client.isFetching()).toBe(0));
  expect(query.reads[0]!.signal.aborted).toBe(true);
  expect(query.reads).toHaveLength(1);
});

it("lets reconnect reset cancel the old request and drops its old trailing refresh", async () => {
  const { client, refresh, observe } = fixture();
  const query = observe(["measurement-slice", "run", "slice"]);
  refresh.invalidate("run");
  refresh.invalidate("run");
  refresh.reset();
  const resetting = client.resetQueries({ queryKey: ["measurement-slice"] });
  await vi.waitFor(() => expect(query.reads).toHaveLength(2));
  expect(query.reads[0]!.signal.aborted).toBe(true);
  query.reads[1]!.resolve(5);
  await resetting;
  await vi.waitFor(() => expect(client.isFetching()).toBe(0));
  expect(query.reads).toHaveLength(2);
  expect(client.getQueryData(query.key)).toBe(5);
  refresh.invalidate("run");
  await vi.waitFor(() => expect(query.reads).toHaveLength(3));
  query.reads[2]!.resolve(6);
  await vi.waitFor(() => expect(client.getQueryData(query.key)).toBe(6));
});

it("invalidates inactive identities without starting requests", async () => {
  const { client, refresh } = fixture();
  const key = ["measurement-slice", "run", "inactive"];
  client.setQueryData(key, 9);
  refresh.invalidate("run");
  expect(client.getQueryState(key)?.isInvalidated).toBe(true);
  expect(client.isFetching()).toBe(0);
});

it.each(["mount", "poll", "reconnect"] as const)(
  "refreshes after an external %s read when a single notification arrives mid-flight",
  async (source) => {
    const { client, refresh, observe } = fixture();
    const query = observe(["measurement-slice", "run", "slice"], {
      mount: source === "mount",
      poll: source === "poll",
    });
    let resetting: Promise<void> | undefined;
    if (source === "reconnect") {
      refresh.reset();
      resetting = client.resetQueries({ queryKey: ["measurement-slice"] });
    }
    await vi.waitFor(() => expect(query.reads).toHaveLength(1));
    query.stopPolling();
    // This is the final notification: the existing read contains a pre-event snapshot.
    refresh.invalidate("run");
    expect(query.reads[0]!.signal.aborted).toBe(false);
    expect(query.reads).toHaveLength(1);
    query.reads[0]!.resolve(1);
    await resetting;
    await vi.waitFor(() => expect(query.reads).toHaveLength(2));
    expect(client.getQueryData(query.key)).toBe(1);
    query.reads[1]!.resolve(2);
    await vi.waitFor(() => expect(client.isFetching()).toBe(0));
    expect(client.getQueryData(query.key)).toBe(2);
    expect(query.reads).toHaveLength(2);
  },
);
