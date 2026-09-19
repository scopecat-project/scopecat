import { reviewedFixture } from "../../test/scientific-fixtures";
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { useEffect } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LaunchDraftProvider, useLaunchDraft } from "./LaunchDraft";
import { LaunchForm } from "./LaunchForm";
import { PlanLibrary } from "./PlanLibrary";
import { planDifferences, type PlanRevision } from "./experiment-plans";
import type { LaunchCatalogEntry } from "./launch-api";

const entry: LaunchCatalogEntry = {
  id: "signal",
  version: "1",
  title: "Signal",
  description: "Signal",
  actions: ["preview", "submit"],
  kind: "diagnostic",
  configuration_effect: "none",
  request: { properties: { note: { type: "string", default: "default" } } },
  controls: [],
};
function plan(id: string, name: string): PlanRevision {
  return {
    ref: { plan_id: id, revision: 1, content_hash: `sha256:${id.repeat(64).slice(0, 64)}` },
    name,
    saved_by: "alice",
    saved_at: "2026-09-09T00:00:00Z",
    definition: {
      workspace_id: "legacy",
      scan_mode: "cartesian",
      parameter_sweeps: [],
      experiment: "signal",
      version: "1",
      definition_hash: `sha256:${"c".repeat(64)}`,
      inputs: { note: name },
      control_edits: {},
      selection: {
        subject: { kind: "unbound" },
        configuration: {
          kind: "saved",
          ref: { entry_id: "saved", content_hash: `sha256:${"d".repeat(64)}` },
        },
        batch: { kind: "unscoped" },
      },
      scientific_binding: {
        codec: "scopecat.scientific-binding.v1",
        config_content_hash: `sha256:${"d".repeat(64)}`,
        setup_content_hash: `sha256:${"e".repeat(64)}`,
        subject: { kind: "unbound" },
      },
    },
  };
}
const first = plan("a", "First plan");
const second = plan("b", "Second plan");
const targetPlan: PlanRevision = {
  ...plan("c", "Target plan"),
  definition: {
    ...first.definition,
    selection: {
      ...first.definition.selection,
      subject: {
        kind: "registered_target",
        ref: {
          catalog_id: "project-a",
          target_id: "device-target",
          revision: 3,
          content_hash: `sha256:${"f".repeat(64)}`,
        },
      },
    },
    scientific_binding: {
      ...first.definition.scientific_binding,
      subject: {
        kind: "registered_target",
        ref: {
          catalog_id: "project-a",
          target_id: "device-target",
          revision: 3,
          content_hash: `sha256:${"f".repeat(64)}`,
        },
        content: {
          members: [
            {
              id: "device",
              sample_id: "chip",
              revision: 2,
              content_hash: `sha256:${"a".repeat(64)}`,
            },
          ],
          connections: [],
        },
        sample: {
          role: "subject",
          sample_id: "chip",
          revision: 2,
          content_hash: `sha256:${"a".repeat(64)}`,
          kind: "chip",
          display_name: "Chip",
        },
        projection: [
          { target_entity: { member_id: "device", entity_id: "q0" }, runtime_entity_id: "q0" },
        ],
      },
    },
  },
};
function Harness({
  library = false,
  initialize = true,
}: {
  library?: boolean;
  initialize?: boolean;
}) {
  const context = useLaunchDraft();
  const select = context.select;
  useEffect(() => {
    if (initialize) select(entry);
  }, [select, initialize]);
  return (
    <>
      <button onClick={() => context.select(entry)}>Select current catalog</button>
      <button onClick={() => context.openPlan(first, entry)}>Open first</button>
      <button onClick={() => context.openPlan(second, entry)}>Open second</button>
      <button onClick={() => context.openPlan(targetPlan, entry)}>Open target</button>
      <button
        onClick={() =>
          context.update((current) => ({
            ...current,
            selection: {
              ...current.selection,
              batch: { kind: "declared", id: "previous-page-batch" },
            },
          }))
        }
      >
        Select previous batch
      </button>
      <button
        onClick={() =>
          context.update((draft) => ({
            ...draft,
            revision: draft.revision + 1,
            values: { note: "new edit" },
          }))
        }
      >
        Edit while opening
      </button>
      <button
        onClick={() =>
          void context.submit(
            {
              workspace_id: "legacy",
              scan_mode: "cartesian",
              parameter_sweeps: [],
              action: "submit",
              experiment: "signal",
              version: "1",
              actor: "bob",
              request_key: "original-key",
              expected_request_hash: `sha256:${"a".repeat(64)}`,
              inputs: {},
              reviewed: reviewedFixture({
                kind: "config_registry",
                selector: "active",
                entry_id: "saved",
                config_ref: "saved",
                content_hash: `sha256:${"d".repeat(64)}`,
                registry_generation: 1,
              }),
            },
            "original",
          )
        }
      >
        Unknown submit
      </button>
      <output aria-label="Current draft">{context.draft?.values.note}</output>
      <output aria-label="Current operator">{context.draft?.actor}</output>
      <output aria-label="Original attempt">
        {context.attempt?.status}:{context.attempt?.request.request_key}
      </output>
      {library ? (
        <PlanLibrary />
      ) : (
        context.draft && <LaunchForm entry={entry} onAdmitted={() => {}} catalogReady />
      )}
    </>
  );
}
function setup(library = false, initialize = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <LaunchDraftProvider projectId="project-a">
        <Harness library={library} initialize={initialize} />
      </LaunchDraftProvider>
    </QueryClientProvider>,
  );
}
function reply(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("opening another plan resets the save name, keeps current actor and preserves an unknown original attempt", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      if (input.url.includes("experiment-launcher/submit")) throw new TypeError("lost response");
      return reply({ activation: { generation: 1, entry_id: "saved" } });
    }),
  );
  setup();
  fireEvent.click(screen.getByText("Unknown submit"));
  await waitFor(() =>
    expect(screen.getByLabelText("Original attempt")).toHaveTextContent("unknown:original-key"),
  );
  fireEvent.click(screen.getByText("Open first"));
  expect(screen.getByLabelText("Plan name")).toHaveValue("First plan");
  fireEvent.change(screen.getByLabelText("Plan name"), { target: { value: "Unsaved name" } });
  fireEvent.click(screen.getByText("Open second"));
  expect(screen.getByLabelText("Plan name")).toHaveValue("Second plan");
  expect(screen.getByLabelText("Current operator")).toHaveTextContent("operator");
  expect(screen.getByLabelText("Original attempt")).toHaveTextContent("unknown:original-key");
});

it("a slow older open cannot overwrite a later plan or a new edit", async () => {
  const pending: ((value: Response) => void)[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: Request) => {
      if (input.url.includes("experiment-plans"))
        return Promise.resolve(reply({ items: [first, second] }));
      if (input.url.includes("experiment-launcher"))
        return new Promise<Response>((resolve) => pending.push(resolve));
      return Promise.resolve(reply({ activation: null }));
    }),
  );
  setup(true);
  await screen.findByText("Open First plan r1");
  fireEvent.click(screen.getByText("Open First plan r1"));
  fireEvent.click(screen.getByText("Open Second plan r1"));
  await waitFor(() => expect(pending).toHaveLength(2));
  await act(async () => {
    pending[1]!(reply({ entries: [entry] }));
  });
  expect(screen.getByLabelText("Current draft")).toHaveTextContent("Second plan");
  await act(async () => {
    pending[0]!(reply({ entries: [entry] }));
  });
  expect(screen.getByLabelText("Current draft")).toHaveTextContent("Second plan");
  fireEvent.click(screen.getByText("Open First plan r1"));
  await waitFor(() => expect(pending).toHaveLength(3));
  fireEvent.click(screen.getByText("Edit while opening"));
  await act(async () => {
    pending[2]!(reply({ entries: [entry] }));
  });
  expect(screen.getByLabelText("Current draft")).toHaveTextContent("new edit");
});

it("comparison identifies changed inputs without placing hashes in the ordinary summary", () => {
  const different = {
    ...second,
    definition: {
      ...second.definition,
      definition_hash: `sha256:${"e".repeat(64)}`,
    },
  };
  const changes = planDifferences(first, different);
  expect(changes.some((line) => line.includes("inputs:"))).toBe(true);
  expect(changes).toContain("Definition declaration changed (exact references in details).");
  expect(changes.join(" ")).not.toContain("sha256:");
});

it("an open started without a draft cannot replace the subsequently selected catalog", async () => {
  let finish: ((response: Response) => void) | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: Request) => {
      if (input.url.includes("experiment-plans")) return Promise.resolve(reply({ items: [first] }));
      if (input.url.includes("experiment-launcher"))
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      return Promise.resolve(reply({ activation: null }));
    }),
  );
  setup(true, false);
  await screen.findByText("Open First plan r1");
  fireEvent.click(screen.getByText("Open First plan r1"));
  await waitFor(() => expect(finish).toBeDefined());
  fireEvent.click(screen.getByText("Select current catalog"));
  fireEvent.click(screen.getByText("Edit while opening"));
  await act(async () => {
    finish!(reply({ entries: [entry] }));
  });
  expect(screen.getByLabelText("Current draft")).toHaveTextContent("new edit");
});

it("waits for initial catalog readiness before allowing a saved plan to open", async () => {
  const fetch = vi.fn(async (input: Request) =>
    reply(input.url.includes("experiment-plans") ? { items: [first] } : { entries: [entry] }),
  );
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = (initializing: boolean) => (
    <QueryClientProvider client={client}>
      <LaunchDraftProvider projectId="project-a">
        <PlanLibrary initializing={initializing} />
      </LaunchDraftProvider>
    </QueryClientProvider>
  );
  const rendered = render(view(true));
  const open = await screen.findByRole("button", { name: "Open First plan r1" });
  expect(open).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent("Loading experiments");
  fireEvent.click(open);
  expect(fetch).toHaveBeenCalledTimes(1);
  rendered.rerender(view(false));
  expect(open).toBeEnabled();
  fireEvent.click(open);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

it("preserves a registered target through reopening, preview, submission and plan save", async () => {
  const reviewed = {
    ...reviewedFixture({
      kind: "config_registry",
      selector: "saved",
      entry_id: "saved",
      config_ref: "saved",
      content_hash: `sha256:${"d".repeat(64)}`,
      registry_generation: 1,
    }),
    binding: targetPlan.definition.scientific_binding,
  };
  const posted: { path: string; body: Record<string, unknown> }[] = [];
  const resolutions: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/measurement-targets/resolve")) {
        const subject = targetPlan.definition.scientific_binding.subject;
        if (subject.kind !== "registered_target") throw new Error("Expected target fixture");
        resolutions.push(await request.json());
        return reply({
          ref: subject.ref,
          content: subject.content,
          name: "Saved target",
          description: "",
          actor: "alice",
          note: "",
          recorded_at: "2026-09-19T00:00:00Z",
        });
      }
      if (request.method === "POST") {
        posted.push({ path, body: await request.json() });
        if (path.endsWith("/preview"))
          return reply({
            experiment_id: "signal",
            workspace_id: "legacy",
            definition_hash: first.definition.definition_hash,
            request_hash: `sha256:${"a".repeat(64)}`,
            reviewed,
            point_count: 1,
            summary: "Checked target",
            resolved_inputs: {},
            controls: [],
            resources: [],
            manual_state: {
              event_id: 1,
              binding: {
                request_hash: `sha256:${"a".repeat(64)}`,
                config_source_hash: `sha256:${"b".repeat(64)}`,
              },
            },
          });
        if (path.endsWith("/validity")) return reply({ valid: true, changes: [] });
        if (path.endsWith("/submit")) return reply({ procedure_id: "target-procedure" });
        if (path.endsWith("/experiment-plans")) return reply(targetPlan);
      }
      if (path.endsWith("/experiment-plans")) return reply({ items: [targetPlan] });
      return reply({ activation: { generation: 1, entry_id: "saved" } });
    }),
  );
  setup();
  fireEvent.click(screen.getByText("Select previous batch"));
  fireEvent.click(screen.getByText("Open target"));
  expect(screen.getByLabelText("Selected registered target")).toHaveTextContent("revision 3");
  expect(screen.getByLabelText("Sample ID")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  await screen.findByText("Preview ready");
  const start = screen.getByRole("button", { name: "Start acquisition" });
  await waitFor(() => expect(start).toBeEnabled());
  fireEvent.click(start);
  await waitFor(() =>
    expect(screen.getByLabelText("Original attempt")).toHaveTextContent("confirmed:"),
  );
  const preview = posted.find((item) => item.path.endsWith("/preview"))!.body;
  const submit = posted.find((item) => item.path.endsWith("/submit"))!.body;
  expect(resolutions).toContainEqual(
    targetPlan.definition.selection.subject?.kind === "registered_target"
      ? targetPlan.definition.selection.subject.ref
      : undefined,
  );
  expect(preview.selection).toEqual(targetPlan.definition.selection);
  expect(submit.selection).toEqual(targetPlan.definition.selection);
  expect(submit.reviewed).toEqual(reviewed);
  expect(submit).not.toHaveProperty("sample");
  expect(submit).not.toHaveProperty("config_source");
  fireEvent.click(screen.getByRole("button", { name: "Save new revision" }));
  await waitFor(() =>
    expect(posted.some((item) => item.path.endsWith("/experiment-plans"))).toBe(true),
  );
  expect(
    posted.find((item) => item.path.endsWith("/experiment-plans"))!.body.definition,
  ).toMatchObject({
    selection: targetPlan.definition.selection,
    scientific_binding: reviewed.binding,
  });
});
