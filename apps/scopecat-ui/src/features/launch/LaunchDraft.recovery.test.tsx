// @vitest-environment jsdom
import { useEffect } from "react";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LaunchDraftProvider, useLaunchDraft, invalidateDraft } from "./LaunchDraft";
import type { LaunchCatalogEntry } from "./launch-api";
import type { AttemptRecord } from "./launch-recovery";
import { defaultSelection } from "./scientific-selection";
import {
  installLaunchRecoveryRoutes,
  procedureDefinition,
} from "../../test/launch-recovery-fixture";

const entry: LaunchCatalogEntry = {
  id: "signal",
  version: "1",
  title: "Signal",
  description: "",
  actions: ["preview", "submit"],
  kind: "diagnostic",
  configuration_effect: "none",
  review: null,
  request: { properties: {}, required: [] },
  controls: [],
};
let state: ReturnType<typeof useLaunchDraft>;
function Probe() {
  const value = useLaunchDraft();
  useEffect(() => {
    state = value;
  }, [value]);
  return null;
}
function mount() {
  return render(
    <LaunchDraftProvider projectId="application">
      <Probe />
    </LaunchDraftProvider>,
  );
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("keeps unsaved input reachable when switching is attempted after a failed save", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ items: [] })),
  );
  installLaunchRecoveryRoutes();
  const persisted = globalThis.fetch;
  let offline = false;
  vi.stubGlobal("fetch", (request: Request) =>
    request.url.endsWith("/launch-drafts/save") && offline
      ? Promise.reject(new Error("offline"))
      : persisted(request),
  );
  mount();
  act(() => state.select(entry, false, "legacy"));
  await waitFor(() =>
    expect(state.recovery.status).toBe("Experiment input saved in application data."),
  );
  offline = true;
  act(() =>
    state.update((draft) =>
      invalidateDraft({ ...draft, values: { raw: "invalid but valuable" } }, "edited"),
    ),
  );
  await waitFor(() => expect(state.recovery.status).toContain("not confirmed saved"));
  act(() => state.select({ ...entry, id: "other" }, false, "legacy"));
  expect(state.draft?.experiment).toBe("signal");
  expect(state.draft?.values.raw).toBe("invalid but valuable");
  act(() => state.selectWorkspace("other-source"));
  expect(state.workspaceId).toBe("legacy");
  offline = false;
  await act(async () => {
    await state.recovery.retry();
  });
  await waitFor(() =>
    expect(state.recovery.status).toBe("Experiment input saved in application data."),
  );
  act(() => state.select({ ...entry, id: "other" }, false, "legacy"));
  await waitFor(() => expect(state.draft?.experiment).toBe("other"));
  await waitFor(() =>
    expect(state.recovery.status).toBe("Experiment input saved in application data."),
  );
  act(() => state.select(entry, false, "legacy"));
  await waitFor(() => expect(state.draft?.values.raw).toBe("invalid but valuable"));
});
const attempt = (sequence: number): AttemptRecord => ({
  sequence,
  created_at: "2026-10-08T00:00:00Z",
  definition: procedureDefinition,
  request: {
    action: "submit",
    scan_mode: "cartesian",
    parameter_sweeps: [],
    actor: "operator",
    workspace_id: "legacy",
    experiment: "signal",
    version: "1",
    request_key: `key-${sequence}`,
    selection: defaultSelection(),
  },
});
it("does not let an old recovery lookup replace a new run or a newly selected receipt", async () => {
  let finish!: (response: Response) => void;
  vi.stubGlobal("fetch", (request: Request) =>
    request.url.endsWith("/resolve")
      ? new Promise<Response>((resolve) => {
          finish = resolve;
        })
      : Promise.resolve(Response.json({ items: [] })),
  );
  mount();
  await act(async () => {
    await Promise.resolve();
  });
  act(() => state.recoverAttempt(attempt(1)));
  let checking!: Promise<void>;
  act(() => {
    checking = state.checkSubmission();
  });
  act(() => state.rerun());
  await act(async () => {
    finish(Response.json({ procedure_id: "old-task" }));
    await checking;
  });
  expect(state.attempt).toBeUndefined();
  act(() => state.recoverAttempt(attempt(1)));
  act(() => {
    checking = state.checkSubmission();
  });
  act(() => state.recoverAttempt(attempt(2)));
  await act(async () => {
    finish(Response.json({ detail: "old lookup failed" }, { status: 409 }));
    await checking;
  });
  expect(state.attempt?.sequence).toBe(2);
  expect(state.attempt?.error).not.toContain("old lookup");
});

it("never submits before its immutable original request is acknowledged, including failed retention", async () => {
  let posts = 0;
  vi.stubGlobal("fetch", async () => {
    posts++;
    return Response.json({ procedure_id: "original" });
  });
  installLaunchRecoveryRoutes();
  const persisted = globalThis.fetch;
  let retain!: () => void;
  let retentionFails = true;
  vi.stubGlobal("fetch", async (request: Request) => {
    if (request.url.endsWith("/launch-attempts") && request.method === "POST") {
      if (retentionFails) throw new Error("retention unavailable");
      await new Promise<void>((resolve) => {
        retain = resolve;
      });
    }
    return persisted(request);
  });
  mount();
  act(() => state.select(entry, false, "legacy"));
  await waitFor(() =>
    expect(state.recovery.status).toBe("Experiment input saved in application data."),
  );
  const request = {
    action: "submit" as const,
    workspace_id: "legacy",
    experiment: "signal",
    version: "1",
    actor: "operator",
    request_key: "original-key",
    scan_mode: "cartesian" as const,
    parameter_sweeps: [],
    selection: defaultSelection(),
  };
  await act(async () => {
    await expect(state.submit(request, "declaration", procedureDefinition)).rejects.toThrow(
      "The local daemon did not respond.",
    );
  });
  expect(posts).toBe(0);
  retentionFails = false;
  let submitting!: Promise<string | undefined>;
  act(() => {
    submitting = state.submit(request, "declaration", procedureDefinition);
  });
  await waitFor(() => expect(retain).toBeDefined());
  expect(posts).toBe(0);
  await act(async () => {
    retain();
    expect(await submitting).toBe("original");
  });
  expect(posts).toBe(1);
});

it("explicitly retries unavailable original receipt loading without submitting", async () => {
  let reads = 0;
  vi.stubGlobal("fetch", async (request: Request) => {
    expect(request.method).toBe("GET");
    reads += 1;
    if (reads === 1) throw new Error("offline");
    return Response.json({ items: [attempt(7)] });
  });
  mount();
  await waitFor(() => expect(reads).toBe(1));
  expect(state.attemptsReady).toBe(false);
  act(() => state.retryAttempts());
  await waitFor(() => expect(state.attemptsReady).toBe(true));
  expect(state.attempt?.sequence).toBe(7);
  expect(reads).toBe(2);
});
