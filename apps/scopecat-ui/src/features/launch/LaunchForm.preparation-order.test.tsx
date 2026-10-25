import { installLaunchRecoveryRoutes } from "../../test/launch-recovery-fixture";
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { useEffect, useRef } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { LaunchDraftProvider, useLaunchDraft } from "./LaunchDraft";
import { LaunchForm } from "./LaunchForm";
import type { LaunchCatalogEntry } from "./launch-api";
import type { ParameterDraftView } from "../config/parameter-draft-api";
vi.mock("./MeasurementContext", async () => {
  const { DeviceContextPicker } = await import("./DeviceContextPicker");
  return {
    MeasurementContext: ({
      draft,
      projectId,
      onChange,
    }: import("react").ComponentProps<typeof import("./MeasurementContext").MeasurementContext>) =>
      draft.selection.configuration.kind === "parameters" ? (
        <DeviceContextPicker
          value={draft.selection.configuration}
          projectId={projectId}
          onChange={(configuration) => onChange({ selection: { configuration } })}
        />
      ) : null,
  };
});
vi.mock("./PlanSave", () => ({ PlanSave: () => null }));
const entry: LaunchCatalogEntry = {
  id: "signal",
  version: "1",
  title: "Signal",
  description: "Synthetic signal",
  actions: ["preview", "submit"],
  controls: [],
  request: { properties: {} },
  kind: "diagnostic",
  configuration_effect: "none",
};
const configuration = {
  kind: "parameters" as const,
  ref: { revision_id: "baseline-a", content_hash: "sha256:base" },
  setup: { revision_id: "bench", content_hash: "sha256:bench" },
  overrides: [],
};
const source: ParameterDraftView = {
  head_revision: 3,
  branch_changed: false,
  draft: {
    draft_id: "table-a",
    revision: 3,
    working_branch: "browser",
    base: configuration.ref,
    state: "saved",
    created_at: "2026-10-06T00:00:00Z",
    input: { name: "", actor: "operator", branch: "browser", note: "", values: [] },
  },
};

let context: ReturnType<typeof useLaunchDraft>;
function Form() {
  const current = useLaunchDraft();
  useEffect(() => {
    context = current;
  });
  const initialized = useRef(false);
  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    void current.select(entry).then(() => {
      current.selectConfiguration(configuration, { draft_id: "table-a", revision: 3 });
    });
  }, [current]);
  return current.draft ? <LaunchForm entry={entry} catalogReady onAdmitted={() => {}} /> : null;
}
const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function mount() {
  const preflight = deferred<Response>();
  const resolution = deferred<Response>();
  let holdWorking = false;
  const preflightStarted = deferred<void>();
  const previews: unknown[] = [];
  const fetcher = vi.fn(async (request: Request) => {
    const path = new URL(request.url).pathname;
    if (path.endsWith("/setup/definitions"))
      return Response.json({ items: [{ id: "starter-bench" }] });
    if (path.includes("/setup/revisions/"))
      return Response.json({
        id: "bench",
        content_hash: "sha256:bench",
        resolution: { definition_id: "starter-bench", devices: [] },
      });
    if (path.includes("/setup/resolutions/")) return resolution.promise;
    if (path.endsWith("/experiment-launcher/preview")) {
      previews.push(await request.json());
      return Response.json({ detail: "preview reached" }, { status: 422 });
    }
    if (path.endsWith("/parameter-drafts/table-a")) {
      if (holdWorking) {
        preflightStarted.resolve();
        return preflight.promise;
      }
      return Response.json(source);
    }
    throw new Error(`Unexpected request: ${request.method} ${path}`);
  });
  vi.stubGlobal("fetch", fetcher);
  installLaunchRecoveryRoutes();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <LaunchDraftProvider projectId="test">
        <Form />
      </LaunchDraftProvider>
    </QueryClientProvider>,
  );
  return {
    preflight,
    resolution,
    previews,
    fetcher,
    client,
    hold: () => {
      holdWorking = true;
    },
    preflightStarted: preflightStarted.promise,
  };
}
async function ready() {
  await waitFor(() => expect(screen.getByRole("button", { name: "预览" })).toBeEnabled());
  await waitFor(() =>
    expect(screen.getByLabelText("Experiment setup")).toHaveValue("starter-bench"),
  );
}
it.each(["before click", "during preflight"])(
  "previews when the same setup resolution completes %s",
  async (order) => {
    const test = mount();
    await ready();
    test.hold();
    fireEvent.change(screen.getByLabelText("Experiment setup"), {
      target: { value: "starter-bench" },
    });
    await waitFor(() =>
      expect(test.fetcher.mock.calls.some(([r]) => r.url.includes("/setup/resolutions/"))).toBe(
        true,
      ),
    );
    const settleSetup = async () => {
      await act(async () =>
        test.resolution.resolve(Response.json({ id: "bench", content_hash: "sha256:bench" })),
      );
      await waitFor(() => expect(screen.getByLabelText("Experiment setup")).toBeEnabled());
    };
    if (order === "before click") await settleSetup();
    const revision = context.draft!.revision;
    fireEvent.click(screen.getByRole("button", { name: "预览" }));
    await act(async () => test.preflightStarted);
    expect(context.draft!.pending).toBe("preview");
    if (order === "during preflight") await settleSetup();
    await act(async () => test.preflight.resolve(Response.json(source)));
    await waitFor(() => expect(test.previews).toHaveLength(1));
    expect(test.previews[0]).toMatchObject({ selection: { configuration }, action: "preview" });
    expect(context.draft!.revision).toBe(revision);
  },
);

it.each([
  { event: "unchanged", posts: 1, delta: 0 },
  { event: "changed", posts: 0, delta: 1 },
  { event: "explicit refresh", posts: 0, delta: 1 },
])(
  "checks $event author source while the working-table read is pending",
  async ({ event, posts, delta }) => {
    const test = mount();
    await ready();
    const sourceRevision = { content_hash: "sha256:source-a" };
    act(() => context.sourceObserved("legacy", sourceRevision));
    await ready();
    test.hold();
    fireEvent.click(screen.getByRole("button", { name: "预览" }));
    await act(async () => test.preflightStarted);
    expect(context.draft!.pending).toBe("preview");
    const revision = context.draft!.revision;
    act(() =>
      event === "explicit refresh"
        ? context.authorRefreshed("legacy")
        : context.sourceObserved(
            "legacy",
            event === "changed"
              ? { ...sourceRevision, content_hash: "sha256:source-b" }
              : sourceRevision,
          ),
    );
    await act(async () => test.preflight.resolve(Response.json(source)));
    await waitFor(() => expect(context.draft!.pending).toBe(false));
    expect(test.previews).toHaveLength(posts);
    expect(context.draft!.revision).toBe(revision + delta);
  },
);

it.each([
  { id: "bench", content_hash: "sha256:next" },
  { id: "bench-next", content_hash: "sha256:bench" },
])("still cancels when setup resolution changes to $id / $content_hash", async (selected) => {
  const test = mount();
  await ready();
  test.hold();
  fireEvent.click(screen.getByRole("button", { name: "Recheck device connections" }));
  fireEvent.click(screen.getByRole("button", { name: "预览" }));
  await act(async () => test.preflightStarted);
  await act(async () => test.resolution.resolve(Response.json(selected)));
  await act(async () => test.preflight.resolve(Response.json(source)));
  expect(test.previews).toHaveLength(0);
  expect(context.draft!.pending).toBe(false);
  expect(context.draft!.selection.configuration).toMatchObject({
    setup: { revision_id: selected.id, content_hash: selected.content_hash },
  });
  expect(context.draft!.notice).toBe(
    "Inputs changed. Start will check the new inputs before acquisition.",
  );
});
it("still blocks preview when the preflight read reports newer working input", async () => {
  const test = mount();
  await ready();
  test.hold();
  fireEvent.click(screen.getByRole("button", { name: "预览" }));
  await act(async () => test.preflightStarted);
  await act(async () => test.preflight.resolve(Response.json({ ...source, head_revision: 4 })));
  expect(test.previews).toHaveLength(0);
  expect(context.draft!.error).toMatch(/Working inputs changed/);
  expect(context.draft!.pending).toBe(false);
});

it("preserves a new parameter selection and target when an earlier setup resolution finishes unchanged", async () => {
  const test = mount();
  await ready();
  fireEvent.click(screen.getByRole("button", { name: "Recheck device connections" }));
  await waitFor(() =>
    expect(test.fetcher.mock.calls.some(([r]) => r.url.includes("/setup/resolutions/"))).toBe(true),
  );
  const nextConfiguration = {
    ...configuration,
    ref: { revision_id: "baseline-b", content_hash: "sha256:base-b" },
  };
  const subject = { kind: "sample" as const, sample_id: "sample-b" };
  act(() =>
    context.selectConfiguration(nextConfiguration, { draft_id: "table-a", revision: 3 }, subject),
  );
  await waitFor(() => expect(screen.getByRole("button", { name: "预览" })).toBeEnabled());
  const revision = context.draft!.revision;
  test.hold();
  fireEvent.click(screen.getByRole("button", { name: "预览" }));
  await act(async () => test.preflightStarted);
  await act(async () =>
    test.resolution.resolve(Response.json({ id: "bench", content_hash: "sha256:bench" })),
  );
  await act(async () => test.preflight.resolve(Response.json(source)));
  await waitFor(() => expect(test.previews).toHaveLength(1));
  expect(test.previews[0]).toMatchObject({
    selection: { configuration: nextConfiguration, subject },
  });
  expect(context.draft!.selection).toMatchObject({ configuration: nextConfiguration, subject });
  expect(context.draft!.revision).toBe(revision);
});
