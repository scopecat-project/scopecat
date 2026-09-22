// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { useEffect } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { LaunchDraftProvider, useLaunchDraft } from "./LaunchDraft";
import type { LaunchCatalogEntry } from "./launch-api";

const entry: LaunchCatalogEntry = {
  id: "rabi",
  version: "1",
  title: "Rabi",
  description: "",
  actions: ["preview", "submit"],
  kind: "diagnostic",
  configuration_effect: "none",
  review: null,
  request: { properties: { note: { type: "string", default: "fresh" } }, required: [] },
  controls: [],
};
let state: ReturnType<typeof useLaunchDraft>;
function Probe() {
  const current = useLaunchDraft();
  useEffect(() => {
    state = current;
  }, [current]);
  return null;
}
function mount() {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <LaunchDraftProvider projectId="project">
        <Probe />
      </LaunchDraftProvider>
    </QueryClientProvider>,
  );
}
afterEach(cleanup);
it("keeps an explicit owner before any draft and rejects old-owner catalog callbacks", () => {
  mount();
  const oldSelect = state.select;
  const oldIsCurrent = state.isCurrent;
  act(() => state.selectWorkspace("workspace-B"));
  expect(state.workspaceId).toBe("workspace-B");
  expect(state.draft).toBeUndefined();
  expect(oldIsCurrent(undefined)).toBe(false);
  act(() => oldSelect(entry));
  expect(state.draft).toBeUndefined();
  act(() => state.select(entry));
  expect(state.draft?.workspaceId).toBe("workspace-B");
});
it("switches identical declarations without retaining old inputs or code but preserves measurement choices", () => {
  mount();
  act(() => state.select(entry));
  act(() =>
    state.update((draft) => ({
      ...draft,
      values: { note: "edited" },
      codeRevision: { content_hash: "sha256:old-code" },
      requestKey: "old-key",
      pending: true,
      actor: "Ada",
      collection: "archive",
      selection: {
        ...draft.selection,
        subject: { kind: "sample", sample_id: "chip" },
        batch: { kind: "declared", id: "cooldown" },
      },
    })),
  );
  const original = state.draft!;
  const oldCurrent = state.isCurrent;
  act(() => state.selectWorkspace("workspace-B"));
  expect(oldCurrent(original.revision)).toBe(false);
  expect(state.draft?.experiment).toBe("");
  expect(state.draft?.values).toEqual({});
  expect(state.draft?.codeRevision).toBeUndefined();
  expect(state.draft?.requestKey).toBeUndefined();
  expect(state.draft?.pending).toBe(false);
  act(() => state.select(entry));
  expect(state.draft?.workspaceId).toBe("workspace-B");
  expect(state.draft?.values).toEqual({ note: "fresh" });
  expect(state.draft?.selection).toEqual(original.selection);
  expect(state.draft?.actor).toBe("Ada");
  expect(state.draft?.collection).toBe("archive");
});
it("retains the exact workspace and pinned code during declaration refresh until explicit current-source choice", () => {
  mount();
  act(() => state.selectWorkspace("workspace-B"));
  act(() => state.select(entry));
  act(() =>
    state.update((draft) => ({
      ...draft,
      values: { note: "reviewed" },
      codeRevision: { content_hash: "sha256:pinned" },
    })),
  );
  act(() => state.select({ ...entry, description: "updated declaration" }));
  expect(state.draft?.workspaceId).toBe("workspace-B");
  expect(state.draft?.codeRevision).toEqual({ content_hash: "sha256:pinned" });
  expect(state.draft?.values.note).toBe("reviewed");
  act(() => state.useCurrentSource());
  expect(state.workspaceId).toBe("workspace-B");
  expect(state.draft?.codeRevision).toBeUndefined();
  expect(state.draft?.experiment).toBe("");
  act(() => state.select(entry));
  expect(state.draft?.workspaceId).toBe("workspace-B");
});

it("invalidates same-definition current-source preview keys only for the refreshed owner", () => {
  mount();
  act(() => state.selectWorkspace("workspace-B"));
  act(() => state.select(entry));
  act(() =>
    state.update((draft) => ({
      ...draft,
      values: { note: "keep" },
      requestKey: "reviewed-request",
    })),
  );
  const revision = state.draft!.revision;
  act(() => state.authorRefreshed("workspace-A"));
  expect(state.draft?.revision).toBe(revision);
  expect(state.draft?.requestKey).toBe("reviewed-request");
  act(() => state.authorRefreshed("workspace-B"));
  expect(state.draft?.revision).toBeGreaterThan(revision);
  expect(state.draft?.requestKey).toBeUndefined();
  expect(state.draft?.values.note).toBe("keep");
  act(() =>
    state.update((draft) => ({
      ...draft,
      codeRevision: { content_hash: "sha256:exact" },
      requestKey: "pinned-request",
    })),
  );
  act(() => state.authorRefreshed("workspace-B"));
  expect(state.draft?.requestKey).toBe("pinned-request");
});

it("selects an exact saved configuration before a draft without changing lab defaults", () => {
  mount();
  const ref = { entry_id: "imported-config", content_hash: "sha256:imported" };
  act(() => state.selectConfiguration({ kind: "saved", ref }));
  expect(state.draft).toBeUndefined();
  act(() => state.select(entry));
  expect(state.draft?.selection.configuration).toEqual({ kind: "saved", ref });
  act(() => state.selectContext());
  expect(state.draft?.selection.configuration).toEqual({ kind: "active" });
});
it("selects independent parameters with an exact setup before opening an experiment", () => {
  mount();
  const choice = {
    kind: "parameters" as const,
    ref: { revision_id: "trial", content_hash: "sha256:parameters" },
    setup: { revision_id: "bench", content_hash: "sha256:setup" },
  };
  act(() => state.selectConfiguration(choice));
  act(() => state.select(entry));
  expect(state.draft?.selection.configuration).toEqual(choice);
});

it("changes only a running draft's parameter selection and invalidates its old preview", () => {
  mount();
  act(() => state.select(entry));
  act(() =>
    state.update((draft) => ({
      ...draft,
      actor: "Ada",
      collection: "records",
      requestKey: "old-key",
      selection: {
        ...draft.selection,
        subject: { kind: "sample", sample_id: "chip" },
        batch: { kind: "declared", id: "cooldown" },
      },
    })),
  );
  const original = state.draft!;
  const ref = { entry_id: "imported-config", content_hash: "sha256:imported" };
  act(() => state.selectConfiguration({ kind: "saved", ref }));
  expect(state.draft?.selection).toEqual({
    ...original.selection,
    configuration: { kind: "saved", ref },
  });
  expect(state.draft?.actor).toBe("Ada");
  expect(state.draft?.collection).toBe("records");
  expect(state.draft?.requestKey).toBeUndefined();
  expect(state.selectedContext).toBeUndefined();
});
