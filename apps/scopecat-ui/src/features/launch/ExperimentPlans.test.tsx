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
      experiment: "signal",
      version: "1",
      definition_hash: `sha256:${"c".repeat(64)}`,
      inputs: { note: name },
      control_edits: {},
      configuration: { entry_id: "saved", content_hash: `sha256:${"d".repeat(64)}` },
      overrides: [],
    },
  };
}
const first = plan("a", "First plan");
const second = plan("b", "Second plan");
function Harness({ library = false }: { library?: boolean }) {
  const context = useLaunchDraft();
  useEffect(() => {
    context.select(entry);
  }, [context.select]);
  return (
    <>
      <button onClick={() => context.openPlan(first, entry)}>Open first</button>
      <button onClick={() => context.openPlan(second, entry)}>Open second</button>
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
              action: "submit",
              experiment: "signal",
              version: "1",
              actor: "bob",
              request_key: "original-key",
              expected_request_hash: `sha256:${"a".repeat(64)}`,
              inputs: {},
              overrides: [],
              config_source: {
                kind: "config_registry",
                selector: "active",
                entry_id: "saved",
                config_ref: "saved",
                content_hash: `sha256:${"d".repeat(64)}`,
                registry_generation: 1,
              },
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
function setup(library = false) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <LaunchDraftProvider projectId="project-a">
        <Harness library={library} />
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
    definition: { ...second.definition, definition_hash: `sha256:${"e".repeat(64)}` },
  };
  const changes = planDifferences(first, different);
  expect(changes.some((line) => line.includes("inputs:"))).toBe(true);
  expect(changes).toContain("Definition declaration changed (exact references in details).");
  expect(changes.join(" ")).not.toContain("sha256:");
});
