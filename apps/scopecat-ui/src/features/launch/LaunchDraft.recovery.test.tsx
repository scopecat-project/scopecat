// @vitest-environment jsdom
import { useEffect } from "react";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LaunchDraftProvider, useLaunchDraft, invalidateDraft } from "./LaunchDraft";
import type { LaunchCatalogEntry } from "./launch-api";
import { attemptRequest, type AttemptRecord } from "./launch-recovery";
import { prepareLaunchClose, releaseLaunchClose } from "./launch-close-barrier";
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
afterEach(async () => {
  cleanup();
  // Let debounced application saves finish before replacing this test's transport.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 300));
  });
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
  await act(async () => state.select(entry, false, "legacy"));
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
  await act(async () => state.select({ ...entry, id: "other" }, false, "legacy"));
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
  await act(async () => state.select({ ...entry, id: "other" }, false, "legacy"));
  await waitFor(() => expect(state.draft?.experiment).toBe("other"));
  await waitFor(() =>
    expect(state.recovery.status).toBe("Experiment input saved in application data."),
  );
  await act(async () => state.select(entry, false, "legacy"));
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
  await act(async () => state.select(entry, false, "legacy"));
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

it.each(["flush", "retain"] as const)(
  "fences duplicate calls and cancelled intent while awaiting %s",
  async (phase) => {
    let submits = 0;
    vi.stubGlobal("fetch", async () => {
      submits++;
      return Response.json({ procedure_id: "original" });
    });
    installLaunchRecoveryRoutes();
    const persisted = globalThis.fetch;
    let release!: () => void;
    let held = false;
    vi.stubGlobal("fetch", async (request: Request) => {
      if (
        held &&
        request.method === "POST" &&
        request.url.endsWith(phase === "flush" ? "/launch-drafts/save" : "/launch-attempts")
      ) {
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      }
      return persisted(request);
    });
    mount();
    await act(async () => state.select(entry, false, "legacy"));
    await waitFor(() =>
      expect(state.recovery.status).toBe("Experiment input saved in application data."),
    );
    held = true;
    if (phase === "flush")
      act(() => state.update((current) => ({ ...current, values: { raw: "changed" } })));
    const request = attemptRequest(attempt(1));
    let sending!: Promise<string | undefined>;
    act(() => {
      sending = state.submit(request, "declaration", procedureDefinition);
    });
    await act(async () => {
      await expect(state.submit(request, "declaration", procedureDefinition)).rejects.toThrow(
        "already being prepared",
      );
    });
    await waitFor(() => expect(release).toBeDefined());
    act(() => state.rerun());
    held = false;
    await act(async () => {
      release();
      expect(await sending).toBeUndefined();
    });
    expect(submits).toBe(0);
  },
);

it.each(["receipt", "edit", "unmount"] as const)(
  "does not acquire after %s changes intent during retention",
  async (change) => {
    let submits = 0;
    vi.stubGlobal("fetch", async () => {
      submits++;
      return Response.json({ procedure_id: "original" });
    });
    installLaunchRecoveryRoutes();
    const persisted = globalThis.fetch;
    let release!: () => void;
    vi.stubGlobal("fetch", async (request: Request) => {
      if (request.method === "POST" && request.url.endsWith("/launch-attempts"))
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      return persisted(request);
    });
    const view = mount();
    await act(async () => state.select(entry, false, "legacy"));
    await waitFor(() =>
      expect(state.recovery.status).toBe("Experiment input saved in application data."),
    );
    let sending!: Promise<string | undefined>;
    act(() => {
      sending = state.submit(attemptRequest(attempt(1)), "declaration", procedureDefinition);
    });
    await waitFor(() => expect(release).toBeDefined());
    act(() => {
      if (change === "receipt") state.recoverAttempt(attempt(7));
      else if (change === "edit")
        state.update((current) =>
          invalidateDraft({ ...current, values: { raw: "new intent" } }, "changed"),
        );
      else view.unmount();
    });
    await act(async () => {
      release();
      expect(await sending).toBeUndefined();
    });
    expect(submits).toBe(0);
    expect(change === "receipt" ? state.attempt?.sequence : submits).toBe(
      change === "receipt" ? 7 : 0,
    );
  },
);

it("a late default lookup cannot replace the user's later selection of the current experiment", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ items: [] })),
  );
  installLaunchRecoveryRoutes();
  mount();
  await act(async () => state.select(entry, false, "legacy"));
  await waitFor(() => expect(state.recovery.ready).toBe(true));
  const transport = globalThis.fetch;
  let finish!: (response: Response) => void;
  vi.stubGlobal("fetch", (request: Request) =>
    request.url.endsWith("/operator-settings")
      ? new Promise<Response>((resolve) => {
          finish = resolve;
        })
      : transport(request),
  );
  let selecting: unknown;
  act(() => {
    selecting = state.select({ ...entry, id: "other" }, false, "legacy");
  });
  await waitFor(() => expect(finish).toBeDefined());
  expect(state.isCurrent(state.draft?.revision)).toBe(false);
  expect(state.beginPreparation()).toBeUndefined();
  await act(async () => state.select(entry, false, "legacy"));
  await act(async () => {
    finish(Response.json({ revision: 2, operator: "Bob" }));
    await selecting;
  });
  expect(state.draft?.experiment).toBe("signal");
  expect(state.draft?.actor).toBe("operator");
});

it("refreshes automatic defaults for a new draft but retains an explicit edit back to the old name", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ items: [] })),
  );
  installLaunchRecoveryRoutes();
  const transport = globalThis.fetch;
  let operator = "Alice";
  vi.stubGlobal("fetch", (request: Request) =>
    request.url.endsWith("/operator-settings")
      ? Promise.resolve(Response.json({ revision: 1, operator }))
      : transport(request),
  );
  mount();
  await act(async () => state.select(entry, false, "legacy"));
  expect(state.draft?.actor).toBe("Alice");
  operator = "Bob";
  await act(async () => state.select(entry, true, "legacy"));
  expect(state.draft?.actor).toBe("Bob");
  act(() => state.update((draft) => ({ ...draft, actor: "Alice" })));
  act(() => state.update((draft) => ({ ...draft, actor: "Bob" })));
  operator = "Carol";
  await act(async () => state.select(entry, true, "legacy"));
  expect(state.draft?.actor).toBe("Bob");
});

it("keeps named copies independent through switching and restart without submitting", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ items: [] })),
  );
  installLaunchRecoveryRoutes();
  const savedFetch = globalThis.fetch;
  const requests: string[] = [];
  const heads = new Map<string, import("./launch-recovery").DraftRecord>();
  vi.stubGlobal("fetch", async (request: Request) => {
    requests.push(request.url);
    const response = await savedFetch(request);
    if (request.url.endsWith("/launch-drafts/save")) {
      const result = await response.clone().json();
      heads.set(result.saved.target.draft_id, result.saved);
    }
    return response;
  });
  const first = mount();
  await act(async () => state.select(entry, false, "legacy"));
  await waitFor(() => expect(state.recovery.status).toContain("input saved"));
  act(() => state.renameDraft("B"));
  act(() =>
    state.update((draft) => invalidateDraft({ ...draft, values: { raw: "B input" } }, "edited")),
  );
  await waitFor(() => expect(heads.get("default")?.input.values.raw).toBe("B input"));
  await act(async () => state.copyDraft());
  const copyId = state.draft?.draftId;
  expect(copyId).not.toBe("default");
  expect(new URL(window.location.href).searchParams.get("draft")).toBe(copyId);
  expect(state.draft?.values.raw).toBe("B input");
  expect(state.draft?.preview).toBeUndefined();
  expect(state.draft?.requestKey).toBeUndefined();
  await waitFor(() => expect(heads.has(copyId!)).toBe(true));
  act(() => state.renameDraft("C"));
  act(() =>
    state.update((draft) => invalidateDraft({ ...draft, values: { raw: "C input" } }, "edited")),
  );
  await waitFor(() => expect(heads.get(copyId!)?.input.values.raw).toBe("C input"));
  await act(async () => state.switchDraft(heads.get("default")!));
  await waitFor(() => expect(state.draft?.values.raw).toBe("B input"));
  expect(state.draft?.draftName).toBe("B");
  expect(new URL(window.location.href).searchParams.get("draft")).toBe("default");
  first.unmount();
  mount();
  await act(async () => state.select(entry, false, "legacy"));
  await waitFor(() => expect(state.draft?.values.raw).toBe("B input"));
  await act(async () => state.switchDraft(heads.get(copyId!)!));
  await waitFor(() => expect(state.draft?.values.raw).toBe("C input"));
  expect(state.draft?.draftName).toBe("C");
  expect(new URL(window.location.href).searchParams.get("draft")).toBe(copyId);
  expect(requests.some((url) => url.endsWith("/experiment-launcher/submit"))).toBe(false);
});

it("refuses to copy a draft whose latest input has not been confirmed saved", async () => {
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
  await act(async () => state.select(entry, false, "legacy"));
  await waitFor(() => expect(state.recovery.status).toContain("input saved"));
  offline = true;
  act(() =>
    state.update((draft) => invalidateDraft({ ...draft, values: { raw: "unsaved B" } }, "edited")),
  );
  await waitFor(() => expect(state.recovery.status).toContain("not confirmed saved"));
  await act(async () => state.copyDraft());
  expect(state.draft?.draftId).toBe("default");
  expect(state.draft?.values.raw).toBe("unsaved B");
});

it("imports historical inputs into a new identity and preserves the existing saved draft", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ items: [] })),
  );
  installLaunchRecoveryRoutes();
  mount();
  const editable = {
    ...entry,
    request: { properties: { note: { type: "string", default: "" } }, required: [] },
  };
  await act(async () => state.select(editable, false, "legacy"));
  await waitFor(() => expect(state.recovery.status).toContain("input saved"));
  act(() =>
    state.update((draft) => invalidateDraft({ ...draft, values: { note: "B work" } }, "edited")),
  );
  await waitFor(() => expect(state.recovery.writing).toBe(false));
  await act(async () => state.recovery.flush());
  const { readDraft } = await import("./launch-recovery");
  const original = (await readDraft("legacy", "signal")).head!;
  await act(async () => {
    expect(
      await state.importHandoff(editable, {
        kind: "handoff",
        source_run: "run-A",
        source_analysis: "analysis-A",
        source_hash: "sha256:A",
        request: {
          workspace_id: "legacy",
          experiment: "signal",
          version: "1",
          action: "preview",
          request_key: "",
          scan_mode: "cartesian",
          parameter_sweeps: [],
          actor: "operator",
          selection: defaultSelection(),
          inputs: { note: "historical A" },
        },
      }),
    ).toBe(true);
  });
  expect(state.draft?.draftId).not.toBe("default");
  expect(state.draft?.values.note).toBe("historical A");
  expect(new URL(window.location.href).searchParams.get("draft")).toBe(state.draft?.draftId);
  await waitFor(() => expect(state.recovery.status).toContain("input saved"));
  const retained = (await readDraft("legacy", "signal")).head!;
  expect(retained.revision).toBe(original.revision);
  expect(retained.input.values.note).toBe("B work");
});

const historicalSource = {
  kind: "parameter_revision" as const,
  parameters: { revision_id: "historical-parameters", content_hash: "sha256:parameters" },
  setup: { revision_id: "historical-setup", content_hash: "sha256:setup" },
  content_hash: "sha256:composed",
  overrides: [
    {
      kind: "update_parameter_rows" as const,
      parameter_id: "frequencies",
      key: { target: "q1" },
      values: { frequency: { value: 5, unit: "GHz" } },
    },
  ],
};
async function configurationCopyFixture() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ items: [] })),
  );
  installLaunchRecoveryRoutes();
  mount();
  await act(async () => state.select(entry, false, "legacy"));
  await waitFor(() => expect(state.recovery.status).toContain("input saved"));
  act(() =>
    state.update((draft) => invalidateDraft({ ...draft, values: { raw: "B input" } }, "edited")),
  );
  await act(async () => state.recovery.flush());
  return globalThis.fetch;
}

it("persists a new draft with exact historical configuration without changing B or submitting", async () => {
  const transport = await configurationCopyFixture();
  const { readDraft } = await import("./launch-recovery");
  const original = (await readDraft("legacy", "signal")).head!;
  const requests: string[] = [];
  vi.stubGlobal("fetch", (request: Request) => {
    requests.push(new URL(request.url).pathname);
    return transport(request);
  });
  let result: Awaited<ReturnType<typeof state.copyHistoricalConfiguration>> | undefined;
  await act(async () => {
    result = await state.copyHistoricalConfiguration("run-A", historicalSource);
  });
  expect(result?.selected).toBe(true);
  expect(state.draft?.draftId).not.toBe("default");
  expect(state.draft?.values.raw).toBe("B input");
  expect(state.draft?.draftName).toBe("Configuration from run-A");
  expect(state.draft?.selection.configuration).toEqual({
    kind: "parameters",
    ref: historicalSource.parameters,
    setup: historicalSource.setup,
    overrides: historicalSource.overrides,
  });
  expect(state.draft?.preview).toBeUndefined();
  expect(state.draft?.requestKey).toBeUndefined();
  expect(state.draft?.needsReview).toBe(true);
  expect((await readDraft("legacy", "signal")).head).toEqual(original);
  expect(requests.every((path) => path.includes("launch-drafts"))).toBe(true);
});

it("preserves B on a lost new-copy response and retries the exact save identity", async () => {
  const transport = await configurationCopyFixture();
  const original = state.draft;
  const commands: unknown[] = [];
  let lost = true;
  let saved: Response | undefined;
  vi.stubGlobal("fetch", async (request: Request) => {
    if (request.url.endsWith("/launch-drafts/save")) {
      const command = await request.clone().json();
      if (command.target.draft_id !== "default") {
        commands.push(command);
        if (saved) return saved.clone();
        saved = await transport(request);
        if (lost) {
          lost = false;
          throw new Error("response lost");
        }
        return saved.clone();
      }
    }
    return transport(request);
  });
  await act(async () => {
    await expect(state.copyHistoricalConfiguration("run-A", historicalSource)).rejects.toThrow(
      "The local daemon did not respond.",
    );
  });
  expect(state.draft).toBe(original);
  await act(async () => {
    await state.copyHistoricalConfiguration("run-A", historicalSource);
  });
  expect(commands).toHaveLength(2);
  expect(commands[1]).toEqual(commands[0]);
  expect(state.draft?.draftId).not.toBe("default");
});

it("retains a saved historical configuration copy separately when B changes during save", async () => {
  const transport = await configurationCopyFixture();
  let release!: () => void;
  vi.stubGlobal("fetch", async (request: Request) => {
    if (
      request.url.endsWith("/launch-drafts/save") &&
      (await request.clone().json()).target.draft_id !== "default"
    )
      await new Promise<void>((resolve) => {
        release = resolve;
      });
    return transport(request);
  });
  let pending!: ReturnType<typeof state.copyHistoricalConfiguration>;
  act(() => {
    pending = state.copyHistoricalConfiguration("run-A", historicalSource);
  });
  await waitFor(() => expect(release).toBeDefined());
  act(() =>
    state.update((draft) => invalidateDraft({ ...draft, values: { raw: "later B" } }, "edited")),
  );
  let result: Awaited<typeof pending> | undefined;
  await act(async () => {
    release();
    result = await pending;
  });
  expect(result?.selected).toBe(false);
  expect(state.draft?.draftId).toBe("default");
  expect(state.draft?.values.raw).toBe("later B");
  const { readDraft } = await import("./launch-recovery");
  const retained = await readDraft("legacy", "signal", result!.target.draft_id);
  expect(retained.head?.input.values.raw).toBe("B input");
  expect(retained.head?.input.selection.configuration).toEqual({
    kind: "parameters",
    ref: historicalSource.parameters,
    setup: historicalSource.setup,
    overrides: historicalSource.overrides,
  });
});

it("rejects unsupported historical configuration without saving or changing B", async () => {
  const transport = await configurationCopyFixture();
  const original = state.draft;
  const writes: string[] = [];
  vi.stubGlobal("fetch", (request: Request) => {
    if (request.method !== "GET") writes.push(request.url);
    return transport(request);
  });
  await act(async () => {
    await expect(
      state.copyHistoricalConfiguration("run-A", {
        kind: "config_registry",
        config_ref: "legacy",
        entry_id: "legacy-entry",
        selector: "default",
        content_hash: "hash",
      }),
    ).rejects.toThrow("no reusable parameter-version or candidate configuration");
  });
  expect(state.draft).toBe(original);
  expect(writes).toEqual([]);
});

it("rejects a historical configuration copy initiated while native close holds the saved input", async () => {
  const transport = await configurationCopyFixture();
  const original = state.draft;
  const writes: string[] = [];
  vi.stubGlobal("fetch", (request: Request) => {
    if (request.url.endsWith("/launch-drafts/save")) writes.push(request.url);
    return transport(request);
  });
  await act(async () => {
    expect(await prepareLaunchClose("copy-init")).toBe("saved");
  });
  await act(async () => {
    await expect(state.copyHistoricalConfiguration("run-A", historicalSource)).rejects.toThrow(
      "Finish saving",
    );
  });
  expect(writes).toHaveLength(0);
  expect(state.draft).toBe(original);
  act(() => releaseLaunchClose("copy-init"));
});

it("retains an already-sent historical copy without replacing the close-certified draft", async () => {
  const transport = await configurationCopyFixture();
  const original = state.draft;
  const originalLocation = window.location.href;
  let release!: () => void;
  vi.stubGlobal("fetch", async (request: Request) => {
    if (
      request.url.endsWith("/launch-drafts/save") &&
      (await request.clone().json()).target.draft_id !== "default"
    )
      await new Promise<void>((resolve) => {
        release = resolve;
      });
    return transport(request);
  });
  let pending!: ReturnType<typeof state.copyHistoricalConfiguration>;
  act(() => {
    pending = state.copyHistoricalConfiguration("run-A", historicalSource);
  });
  await waitFor(() => expect(release).toBeDefined());
  await act(async () => {
    expect(await prepareLaunchClose("copy-finish")).toBe("saved");
  });
  let result: Awaited<typeof pending> | undefined;
  await act(async () => {
    release();
    result = await pending;
  });
  expect(result?.selected).toBe(false);
  expect(state.draft).toBe(original);
  expect(window.location.href).toBe(originalLocation);
  const { readDraft } = await import("./launch-recovery");
  const saved = await readDraft("legacy", "signal", result!.target.draft_id);
  expect(saved.head?.input.values.raw).toBe("B input");
  expect(saved.head?.input.selection.configuration).toEqual({
    kind: "parameters",
    ref: historicalSource.parameters,
    setup: historicalSource.setup,
    overrides: historicalSource.overrides,
  });
  act(() => releaseLaunchClose("copy-finish"));
});

it.each(["handoff", "plan"] as const)(
  "does not navigate to a late %s import while native close holds B",
  async (mode) => {
    const transport = await configurationCopyFixture();
    const original = state.draft;
    const location = window.location.href;
    let finish!: (response: Response) => void;
    vi.stubGlobal("fetch", (request: Request) =>
      request.url.endsWith("/operator-settings")
        ? new Promise<Response>((resolve) => {
            finish = resolve;
          })
        : transport(request),
    );
    let pending!: Promise<boolean>;
    act(() => {
      pending =
        mode === "plan"
          ? state.openPlan(
              {
                ref: { plan_id: "saved-plan", revision: 1, content_hash: "sha256:plan" },
                name: "Saved plan",
                saved_by: "Alice",
                saved_at: "2026-10-01T00:00:00Z",
                definition: {
                  workspace_id: "other-source",
                  experiment: "signal",
                  version: "1",
                  definition_hash: "sha256:definition",
                  scan_mode: "cartesian",
                  parameter_sweeps: [],
                  inputs: {},
                  control_edits: {},
                  selection: defaultSelection(),
                  scientific_binding: {
                    codec: "scopecat.scientific-binding.v3",
                    config_content_hash: "sha256:config",
                    setup_content_hash: "sha256:setup",
                    subject: { kind: "unbound" },
                  },
                },
              },
              entry,
            )
          : state.importHandoff(entry, {
              kind: "handoff",
              source_run: "run-A",
              source_analysis: "analysis-A",
              source_hash: "sha256:A",
              request: {
                workspace_id: "other-source",
                experiment: "signal",
                version: "1",
                action: "preview",
                request_key: "",
                scan_mode: "cartesian",
                parameter_sweeps: [],
                actor: "operator",
                selection: defaultSelection(),
                inputs: {},
              },
            });
    });
    await waitFor(() => expect(finish).toBeDefined());
    await act(async () => {
      expect(await prepareLaunchClose("late-import")).toBe("saved");
    });
    let selected: boolean | undefined;
    await act(async () => {
      finish(Response.json({ revision: 2, operator: "Bob" }));
      selected = await pending;
    });
    expect(selected).toBe(false);
    expect(state.draft).toBe(original);
    expect(state.workspaceId).toBe("legacy");
    expect(window.location.href).toBe(location);
    act(() => releaseLaunchClose("late-import"));
  },
);

it("hydrates saved raw inputs after automatic source initialization during a delayed read", async () => {
  const transport = await configurationCopyFixture();
  act(() =>
    state.update((draft) => ({
      ...draft,
      actor: "Alice",
      values: { sample: "chip-a" },
      controls: { position: { mode: "values", values: "1\n2", unit: "mm" } },
    })),
  );
  await act(async () => state.recovery.flush());
  cleanup();
  const { resetLaunchRecoveryWindowForTests } = await import("./launch-recovery");
  resetLaunchRecoveryWindowForTests();
  let finish!: () => void;
  const reading = new Promise<void>((resolve) => {
    finish = resolve;
  });
  vi.stubGlobal("fetch", async (request: Request) => {
    if (request.url.endsWith("/launch-drafts/read")) await reading;
    return transport(request);
  });
  mount();
  await act(async () => state.select(entry, false, "legacy"));
  expect(state.recovery.ready).toBe(false);
  act(() => state.sourceObserved("legacy", { content_hash: "sha256:current-catalog" }));
  await act(async () => finish());
  await waitFor(() => expect(state.recovery.ready).toBe(true));
  expect(state.draft?.actor).toBe("Alice");
  expect(state.draft?.values.sample).toBe("chip-a");
  expect(state.draft?.controls.position).toMatchObject({
    mode: "values",
    values: "1\n2",
    unit: "mm",
  });
  expect(state.draft?.needsReview).toBe(true);
  expect(state.recovery.conflict).toBe(false);
});

it("retains an explicit current-source choice during initial recovery instead of restoring the pin", async () => {
  const transport = await configurationCopyFixture();
  act(() =>
    state.update((draft) => ({ ...draft, codeRevision: { content_hash: "sha256:pinned" } })),
  );
  await act(async () => state.recovery.flush());
  const { readDraft, resetLaunchRecoveryWindowForTests } = await import("./launch-recovery");
  const original = (await readDraft("legacy", "signal")).head!;
  cleanup();
  resetLaunchRecoveryWindowForTests();
  let finish!: () => void;
  const reading = new Promise<void>((resolve) => {
    finish = resolve;
  });
  vi.stubGlobal("fetch", async (request: Request) => {
    if (request.url.endsWith("/launch-drafts/read")) await reading;
    if (request.url.endsWith("/launch-drafts/save")) {
      const command = await request.clone().json();
      if (command.expected_revision !== original.revision)
        return Response.json({
          head: original,
          saved: {
            ...original,
            input: command.input,
            state: "conflict",
            revision: original.revision + 1,
          },
        });
    }
    return transport(request);
  });
  mount();
  await act(async () => {
    expect(await state.switchDraft(original)).toBe(true);
  });
  expect(state.recovery.ready).toBe(false);
  expect(state.draft?.codeRevision?.content_hash).toBe("sha256:pinned");
  act(() => state.selectCurrentSource("legacy"));
  await act(async () => finish());
  await waitFor(() => expect(state.recovery.conflict).toBe(true));
  expect(state.draft?.codeRevision).toBeUndefined();
  expect((await readDraft("legacy", "signal")).head).toEqual(original);
});
