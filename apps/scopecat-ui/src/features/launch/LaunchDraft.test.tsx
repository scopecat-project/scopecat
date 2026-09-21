import { serviceWorkspaceCatalog } from "../../test/scientific-fixtures";
import { targetRefKey } from "./target-api";
import type { SubmissionRequest } from "./launch-submission";
import { reviewedFixture } from "../../test/scientific-fixtures";
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { LaunchCatalogEntry } from "./launch-api";
import { LaunchDraftProvider } from "./LaunchDraft";
import { LaunchWorkspace } from "./LaunchWorkspace";

const prepared: LaunchCatalogEntry = {
  id: "prepared",
  version: "1",
  title: "Prepared experiment",
  description: "Prepare numeric controls",
  actions: ["preview", "submit"],
  kind: "diagnostic",
  configuration_effect: "none",
  review: null,
  request: {
    properties: { note: { type: "string", title: "Note", default: "original" } },
    required: [],
  },
  controls: [
    {
      id: "frequency",
      title: "Frequency",
      group: "Signal",
      default: { value: 4.8, unit: "GHz" },
      unit: "GHz",
      minimum: 4,
      maximum: 6,
      scannable: true,
      ownership: "editable",
      provenance: "Project default",
    },
  ],
};
let catalog: LaunchCatalogEntry[];
let deferCatalog: boolean;
let catalogResponse: ((response: Response) => void) | undefined;
let generation: number;
let fixedSource: boolean;
let manualEventId: number;
let configFails: boolean;
let rejectSubmission: boolean;
let submissions: SubmissionRequest[];
let previewResponse: ((response: Response) => void) | undefined;
let deferPreview: boolean;
let configurationResponse: ((response: Response) => void) | undefined;
let deferConfiguration: boolean;
let client: QueryClient;
let lookupMatch: "none" | "original" | "ambiguous" | "unverified" | "different-config";
function preview() {
  return {
    experiment_id: "prepared",
    request_hash: `sha256:${"a".repeat(64)}`,
    manual_state: {
      event_id: ++manualEventId,
      binding: {
        request_hash: `sha256:${"a".repeat(64)}`,
        config_source_hash: `sha256:${"b".repeat(64)}`,
        code_revision: null,
      },
    },
    point_count: 1,
    reviewed: reviewedFixture({
      kind: "config_registry",
      selector: fixedSource ? "baseline" : "active",
      entry_id: "baseline",
      config_ref: "baseline",
      content_hash: `sha256:${"b".repeat(64)}`,
      registry_generation: fixedSource ? null : generation,
    }),
    summary: "Checked preparation",
    resources: [],
    resolved_inputs: {},
    controls: [],
    preflight: null,
  };
}
function Harness({ projectId = "project-a" }: { projectId?: string }) {
  const [page, setPage] = useState("launch");
  return (
    <QueryClientProvider client={client}>
      <LaunchDraftProvider projectId={projectId}>
        <nav>
          {["launch", "configuration", "devices", "results"].map((name) => (
            <button key={name} onClick={() => setPage(name)}>
              {name}
            </button>
          ))}
        </nav>
        {page === "launch" ? <LaunchWorkspace /> : <p>{page} workspace</p>}
      </LaunchDraftProvider>
    </QueryClientProvider>
  );
}
beforeEach(() => {
  catalog = [{ ...prepared, id: "first", title: "Default experiment" }, prepared];
  deferCatalog = false;
  catalogResponse = undefined;
  lookupMatch = "none";
  generation = 1;
  fixedSource = false;
  manualEventId = 0;
  configFails = false;
  rejectSubmission = false;
  submissions = [];
  previewResponse = undefined;
  deferPreview = false;
  deferConfiguration = false;
  configurationResponse = undefined;
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      if (path.endsWith("/author-workspaces")) return Response.json(serviceWorkspaceCatalog);
      if (path.endsWith("/procedures") && url.searchParams.has("request_key")) {
        const original = submissions[0];
        const item = {
          procedure_run_id: "original-procedure",
          request_key: original?.request_key,
          definition: { id: "maintained.launch_prepared", version: "1" },
          scientific_binding: original?.reviewed?.binding,
          intent: {
            request_hash: lookupMatch === "unverified" ? "other" : original?.expected_request_hash,
            config_source:
              lookupMatch === "different-config"
                ? {
                    ...original?.reviewed?.config_source,
                    registry_generation: 999,
                  }
                : original?.reviewed?.config_source,
          },
        };
        return Response.json({
          items:
            lookupMatch === "none"
              ? []
              : lookupMatch === "ambiguous"
                ? [
                    item,
                    {
                      ...item,
                      procedure_run_id: "other-procedure",
                      definition: { id: "different-definition", version: "1" },
                    },
                  ]
                : [item],
          next_cursor: null,
        });
      }
      if (path.endsWith("/experiment-launcher"))
        return deferCatalog
          ? new Promise<Response>((resolve) => {
              catalogResponse = resolve;
            })
          : Response.json({ entries: catalog });
      if (path.endsWith("/config-registry")) {
        if (deferConfiguration)
          return new Promise<Response>((resolve) => {
            configurationResponse = resolve;
          });
        if (configFails) throw new TypeError("temporarily offline");
        return Response.json({ entries: [], activation: { entry_id: "baseline", generation } });
      }
      if (path.endsWith("/experimental-batches"))
        return Response.json({
          items: [
            { id: "batch-a", name: "Batch A" },
            { id: "batch-b", name: "Batch B" },
          ],
          next_cursor: null,
        });
      if (path.endsWith("/record-collections"))
        return Response.json({
          items: [{ id: "collection-a", name: "Collection A" }],
          next_cursor: null,
        });
      if (path.endsWith("/validity")) return Response.json({ valid: true, changes: [] });
      if (path.endsWith("/preview"))
        return deferPreview
          ? new Promise<Response>((resolve) => {
              previewResponse = resolve;
            })
          : Response.json(preview());
      if (path.endsWith("/submit")) {
        submissions.push(await request.json());
        if (rejectSubmission) return Response.json({ detail: "Rejected input" }, { status: 422 });
        throw new TypeError("response lost");
      }
      return Response.json({ items: [], next_cursor: null });
    }),
  );
});
afterEach(() => {
  cleanup();
  client.clear();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});
async function selectPrepared() {
  fireEvent.change(await screen.findByLabelText("Experiment"), { target: { value: "prepared" } });
  await screen.findByLabelText("Note");
}
async function previewReady() {
  await waitFor(() => expect(screen.getByRole("button", { name: "Preview" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  await screen.findByText("Preview ready", { exact: true });
  await waitFor(() =>
    expect(screen.queryByText("Checking relevant instrument changes…")).toBeNull(),
  );
}
async function returnToLaunch() {
  fireEvent.click(screen.getByRole("button", { name: "launch" }));
  await screen.findByLabelText("Note");
}
it("retains the complete project draft across pages and resets explicitly without submission", async () => {
  render(<Harness />);
  await selectPrepared();
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "prepared note" } });
  fireEvent.change(screen.getByLabelText("Sample ID"), { target: { value: "sample-42" } });
  fireEvent.change(screen.getByLabelText("Operator"), { target: { value: "scientist" } });
  fireEvent.change(screen.getByLabelText("Frequency source"), { target: { value: "range" } });
  fireEvent.change(screen.getByLabelText("Frequency unit"), { target: { value: "MHz" } });
  fireEvent.change(screen.getByLabelText("Frequency start"), { target: { value: "4700" } });
  fireEvent.change(screen.getByLabelText("Frequency stop"), { target: { value: "4900" } });
  fireEvent.change(screen.getByLabelText("Frequency points"), { target: { value: "3" } });
  for (const page of ["configuration", "devices", "results"]) {
    fireEvent.click(screen.getByRole("button", { name: page }));
    await returnToLaunch();
    expect(screen.getByLabelText("Experiment")).toHaveValue("prepared");
    expect(screen.getByLabelText("Note")).toHaveValue("prepared note");
    expect(screen.getByLabelText("Sample ID")).toHaveValue("sample-42");
    expect(screen.getByLabelText("Operator")).toHaveValue("scientist");
    expect(screen.getByLabelText("Frequency source")).toHaveValue("range");
    expect(screen.getByLabelText("Frequency unit")).toHaveValue("MHz");
    expect(screen.getByLabelText("Frequency start")).toHaveValue(4700);
    expect(screen.getByLabelText("Frequency stop")).toHaveValue(4900);
    expect(screen.getByLabelText("Frequency points")).toHaveValue(3);
    expect(screen.queryByText("Preview ready")).toBeNull();
  }
  expect(submissions).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Reset launch draft" }));
  expect(screen.getByLabelText("Note")).toHaveValue("original");
  expect(screen.getByLabelText("Sample ID")).toHaveValue("sample-42");
  expect(screen.getByLabelText("Operator")).toHaveValue("scientist");
  expect(screen.getByLabelText("Frequency")).toHaveValue(4.8);
});
it("keeps an unknown submission key across navigation and temporary configuration read failure", async () => {
  render(<Harness />);
  await selectPrepared();
  await previewReady();
  fireEvent.click(screen.getByRole("button", { name: "Start acquisition" }));
  await screen.findByRole("alert");
  const originalKey = submissions[0]?.request_key;
  fireEvent.click(screen.getByRole("button", { name: "configuration" }));
  configFails = true;
  await returnToLaunch();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Start acquisition" })).toBeDisabled(),
  );
  configFails = false;
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["config"] });
  });
  await screen.findByText("Preview ready", { exact: true });
  await previewReady(); // Previewing the same request does not allocate another submission.
  fireEvent.click(screen.getByRole("button", { name: "Retry original submission" }));
  await waitFor(() => expect(submissions).toHaveLength(2));
  expect(submissions[1]?.request_key).toBe(originalKey);
  expect(submissions[1]?.manual_state).toEqual(submissions[0]?.manual_state);
  await screen.findByRole("alert");
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "changed request" } });
  expect(screen.queryByText("Preview ready")).toBeNull();
  lookupMatch = "original";
  fireEvent.click(screen.getByRole("button", { name: "Check original submission" }));
  await screen.findByRole("button", { name: "Open submitted procedure" });
  await previewReady();
  fireEvent.click(screen.getByRole("button", { name: "Start acquisition" }));
  await waitFor(() => expect(submissions).toHaveLength(3));
  expect(submissions[2]?.request_key).not.toBe(originalKey);
});
it("invalidates previews after configuration or definition changes while retaining editable inputs", async () => {
  render(<Harness />);
  await selectPrepared();
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "keep me" } });
  await previewReady();
  fireEvent.click(screen.getByRole("button", { name: "configuration" }));
  generation = 2;
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["config"] });
  });
  await returnToLaunch();
  expect(screen.getByLabelText("Note")).toHaveValue("keep me");
  expect(screen.queryByText("Preview ready")).toBeNull();
  expect(screen.getByText(/Configuration changed/)).toBeVisible();
  await previewReady();
  catalog = [catalog[0]!, { ...prepared, description: "Updated definition at same version" }];
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["experiment-launcher"] });
  });
  expect(await screen.findByText(/Experiment revision changed/)).toBeVisible();
  expect(screen.getByLabelText("Note")).toHaveValue("keep me");
  expect(screen.queryByText("Preview ready")).toBeNull();
});
it("retains fixed-source previews across unrelated active parameter publication", async () => {
  fixedSource = true;
  render(<Harness />);
  await selectPrepared();
  await previewReady();
  generation = 2;
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["config"] });
  });
  expect(screen.getByText("Preview ready")).toBeVisible();
  expect(screen.getByRole("button", { name: "Start acquisition" })).toBeEnabled();
});
it("does not transfer a draft or late preview when the same console changes project identity", async () => {
  const view = render(<Harness />);
  await selectPrepared();
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "project A only" } });
  deferPreview = true;
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  await waitFor(() => expect(previewResponse).toBeDefined());
  view.rerender(<Harness projectId="project-b" />);
  await screen.findByLabelText("Note");
  expect(screen.getByLabelText("Experiment")).toHaveValue("first");
  expect(screen.getByLabelText("Note")).toHaveValue("original");
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "project B" } });
  await act(async () => {
    previewResponse?.(Response.json(preview()));
  });
  expect(screen.getByLabelText("Note")).toHaveValue("project B");
  expect(screen.queryByText("Preview ready")).toBeNull();
});

it("reopens original admitted work after lost response, configuration and definition changes without resubmitting", async () => {
  render(<Harness />);
  await selectPrepared();
  await previewReady();
  fireEvent.click(screen.getByRole("button", { name: "Start acquisition" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "configuration" }));
  generation = 2;
  catalog = [catalog[0]!, { ...prepared, version: "2" }];
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["config"] });
  });
  await returnToLaunch();
  await screen.findByText(/Experiment revision changed/);
  expect(screen.getByRole("button", { name: "Retry original submission" })).toBeDisabled();
  lookupMatch = "original";
  fireEvent.click(screen.getByRole("button", { name: "Check original submission" }));
  fireEvent.click(await screen.findByRole("button", { name: "Open submitted procedure" }));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(new URLSearchParams(window.location.search).get("procedure")).toBe("original-procedure");
  expect(submissions).toHaveLength(1);
});
it.each(["none", "ambiguous", "unverified", "different-config"] as const)(
  "leaves %s lookup unconfirmed without choosing or resubmitting",
  async (match) => {
    render(<Harness />);
    await selectPrepared();
    await previewReady();
    fireEvent.click(screen.getByRole("button", { name: "Start acquisition" }));
    await screen.findByRole("alert");
    lookupMatch = match;
    fireEvent.click(screen.getByRole("button", { name: "Check original submission" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "Open submitted procedure" })).toBeNull();
    expect(screen.getByRole("button", { name: "Start acquisition" })).toBeDisabled();
    expect(submissions).toHaveLength(1);
  },
);

it("distinguishes a known rejection from an earlier unknown submission", async () => {
  render(<Harness />);
  await selectPrepared();
  await previewReady();
  rejectSubmission = true;
  fireEvent.click(screen.getByRole("button", { name: "Start acquisition" }));
  await screen.findByText("Submission rejected", { exact: true });
  expect(screen.queryByRole("button", { name: "Check original submission" })).toBeNull();
  rejectSubmission = false;
  fireEvent.click(screen.getByRole("button", { name: "Start acquisition" }));
  await screen.findByRole("button", { name: "Retry original submission" });
  rejectSubmission = true;
  fireEvent.click(screen.getByRole("button", { name: "Retry original submission" }));
  await waitFor(() => expect(submissions).toHaveLength(3));
  await screen.findByRole("button", { name: "Check original submission" });
  expect(screen.queryByText("Submission rejected", { exact: true })).toBeNull();
  expect(submissions[2]?.request_key).toBe(submissions[1]?.request_key);
});
it("ignores a late preview after selecting another experiment in the same project", async () => {
  render(<Harness />);
  await selectPrepared();
  deferPreview = true;
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  await waitFor(() => expect(previewResponse).toBeDefined());
  fireEvent.change(screen.getByLabelText("Experiment"), { target: { value: "first" } });
  fireEvent.change(await screen.findByLabelText("Note"), { target: { value: "different draft" } });
  await act(async () => {
    previewResponse?.(Response.json(preview()));
  });
  expect(screen.getByLabelText("Experiment")).toHaveValue("first");
  expect(screen.getByLabelText("Note")).toHaveValue("different draft");
  expect(screen.queryByText("Preview ready")).toBeNull();
});

it("retains an unavailable experiment draft until its declaration returns", async () => {
  render(<Harness />);
  await selectPrepared();
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "keep unavailable inputs" } });
  await previewReady();
  catalog = [catalog[0]!];
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["experiment-launcher"] });
  });
  await screen.findByText(/The selected experiment .* is unavailable/);
  expect(screen.getByLabelText("Experiment")).toHaveValue("prepared");
  expect(screen.queryByText("Preview ready")).toBeNull();
  catalog = [catalog[0]!, prepared];
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["experiment-launcher"] });
  });
  expect(await screen.findByLabelText("Note")).toHaveValue("keep unavailable inputs");
  expect(screen.getByLabelText("Experiment")).toHaveValue("prepared");
  expect(screen.queryByText("Preview ready")).toBeNull();
  expect(submissions).toHaveLength(0);
});

it("retains a scan across helper revisions but resets changed control declarations", async () => {
  render(<Harness />);
  await selectPrepared();
  fireEvent.change(screen.getByLabelText("Frequency source"), { target: { value: "range" } });
  fireEvent.change(screen.getByLabelText("Frequency start"), { target: { value: "4.7" } });
  fireEvent.change(screen.getByLabelText("Frequency stop"), { target: { value: "4.9" } });
  fireEvent.change(screen.getByLabelText("Frequency points"), { target: { value: "3" } });
  await previewReady();
  catalog = [catalog[0]!, { ...prepared, version: "helper-revision-2" }];
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["experiment-launcher"] });
  });
  await screen.findByText(
    "Experiment revision changed. Inputs and control edits are retained; preview again.",
  );
  expect(screen.getByLabelText("Frequency points")).toHaveValue(3);
  expect(screen.getByLabelText("Frequency start")).toHaveValue(4.7);
  expect(screen.queryByText("Preview ready")).toBeNull();
  expect(screen.getByRole("button", { name: "Start acquisition" })).toBeDisabled();
  catalog = [
    catalog[0]!,
    {
      ...prepared,
      version: "controls-revision-3",
      controls: [{ ...prepared.controls[0]!, default: { value: 5, unit: "GHz" } }],
    },
  ];
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["experiment-launcher"] });
  });
  await screen.findByText(
    "Control declarations changed. Check retained inputs and new control defaults, then preview again.",
  );
  expect(screen.getByLabelText("Frequency", { exact: true })).toHaveValue(5);
  expect(screen.queryByLabelText("Frequency points")).toBeNull();
});

it("uses a new key after a fresh manual-state preview while preserving the original attempt", async () => {
  render(<Harness />);
  await selectPrepared();
  await previewReady();
  fireEvent.click(screen.getByRole("button", { name: "Start acquisition" }));
  await screen.findByRole("alert");
  const original = submissions[0];
  lookupMatch = "original";
  fireEvent.click(screen.getByRole("button", { name: "Check original submission" }));
  await screen.findByRole("button", { name: "Open submitted procedure" });
  await previewReady();
  fireEvent.click(screen.getByRole("button", { name: "Start acquisition" }));
  await waitFor(() => expect(submissions).toHaveLength(2));
  expect(submissions[1]?.request_key).not.toBe(original?.request_key);
  expect(submissions[1]?.manual_state).not.toEqual(original?.manual_state);
});

async function refreshCheckedPreview() {
  render(<Harness />);
  await selectPrepared();
  await previewReady();
  const start = screen.getByRole("button", { name: "Start acquisition" });
  await waitFor(() => expect(start).toBeEnabled());
  deferConfiguration = true;
  act(() => {
    void client.invalidateQueries({ queryKey: ["config", "launch-context"] });
  });
  await waitFor(() => expect(configurationResponse).toBeDefined());
  expect(screen.getByText("Preview ready", { exact: true })).toBeVisible();
  expect(start).toBeEnabled();
  return start;
}

it("submits a checked preview while configuration refresh is in flight", async () => {
  const start = await refreshCheckedPreview();
  fireEvent.click(start);
  await waitFor(() => expect(submissions).toHaveLength(1));
  expect(submissions[0]).toMatchObject({
    experiment: "prepared",
    reviewed: { config_source: { entry_id: "baseline", registry_generation: 1 } },
  });
  await act(async () => {
    configurationResponse!(
      Response.json({ entries: [], activation: { entry_id: "baseline", generation: 1 } }),
    );
  });
});

it.each(["changed", "failed"])(
  "blocks a checked preview when background configuration refresh resolves %s",
  async (outcome) => {
    const start = await refreshCheckedPreview();
    await act(async () => {
      configurationResponse!(
        outcome === "failed"
          ? Response.json({ detail: "offline" }, { status: 503 })
          : Response.json({ entries: [], activation: { entry_id: "baseline", generation: 2 } }),
      );
    });
    await waitFor(() => expect(start).toBeDisabled());
    expect(screen.queryByText("Preview ready", { exact: true })).toBeNull();
    expect(submissions).toHaveLength(0);
  },
);

it("keeps preview clickable during a background catalog read and blocks a failed read", async () => {
  render(<Harness />);
  await selectPrepared();
  deferCatalog = true;
  act(() => {
    void client.invalidateQueries({ queryKey: ["experiment-launcher"] });
  });
  await waitFor(() => expect(catalogResponse).toBeDefined());
  const button = screen.getByRole("button", { name: "Preview" });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  await screen.findByText("Preview ready", { exact: true });
  await act(async () => {
    catalogResponse!(Response.json({ detail: "offline" }, { status: 503 }));
  });
  await waitFor(() => expect(button).toBeDisabled());
  expect(screen.queryByText("Preview ready", { exact: true })).toBeNull();
});

it("keeps context across experiments and preserves the original submission after context edits", async () => {
  render(<Harness />);
  await selectPrepared();
  fireEvent.click(screen.getByRole("button", { name: "Browse samples, batches and collections" }));
  await screen.findByRole("option", { name: "Batch A" });
  fireEvent.change(screen.getByLabelText("Sample ID"), { target: { value: "chip-a" } });
  fireEvent.change(screen.getByLabelText("Operator"), { target: { value: "Alice" } });
  fireEvent.change(screen.getByLabelText("Experimental batch"), { target: { value: "batch-a" } });
  fireEvent.change(screen.getByLabelText("Record collection"), {
    target: { value: "collection-a" },
  });
  fireEvent.change(screen.getByLabelText("Experiment"), { target: { value: "first" } });
  expect(screen.getByLabelText("Sample ID")).toHaveValue("chip-a");
  expect(screen.getByLabelText("Experimental batch")).toHaveValue("batch-a");
  expect(screen.getByLabelText("Operator")).toHaveValue("Alice");
  await previewReady();
  fireEvent.click(screen.getByRole("button", { name: "Start acquisition" }));
  await screen.findByRole("alert");
  expect(submissions[0]).toMatchObject({
    selection: {
      subject: { kind: "sample", sample_id: "chip-a" },
      configuration: { kind: "active" },
      batch: { kind: "declared", id: "batch-a" },
    },
    actor: "Alice",
    record_collection: "collection-a",
  });
  fireEvent.change(screen.getByLabelText("Experimental batch"), { target: { value: "batch-b" } });
  expect(screen.queryByText("Preview ready", { exact: true })).toBeNull();
  expect(screen.getByRole("button", { name: "Start acquisition" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Retry original submission" }));
  await waitFor(() => expect(submissions).toHaveLength(2));
  expect(submissions[1]).toEqual(submissions[0]);
  expect(screen.getByLabelText("Experimental batch")).toHaveValue("batch-b");
});

it("pins a picked target across head refresh and pages, then clears it on catalog scope change", async () => {
  const ref = {
    catalog_id: "project-a",
    target_id: "chip-target",
    revision: 1,
    content_hash: `sha256:${"f".repeat(64)}`,
  };
  const target = {
    ref,
    name: "Chip target",
    description: "",
    actor: "Alice",
    note: "",
    recorded_at: "2026-09-19T00:00:00Z",
    content: {
      members: [
        { id: "device", sample_id: "chip", revision: 2, content_hash: `sha256:${"c".repeat(64)}` },
      ],
      connections: [],
    },
  };
  let head = target;
  const fallback = globalThis.fetch;
  const previews: SubmissionRequest[] = [];
  const resolutions: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/measurement-targets"))
        return Response.json({ items: [head], next_cursor: null });
      if (path.endsWith("/measurement-targets/resolve")) {
        resolutions.push(await request.json());
        return Response.json(target);
      }
      if (path.endsWith("/preview")) {
        const body = (await request.json()) as SubmissionRequest;
        previews.push(body);
        const result = preview();
        return Response.json({
          ...result,
          reviewed: {
            ...result.reviewed,
            binding: {
              ...result.reviewed.binding,
              subject: {
                kind: "registered_target",
                ref,
                content: target.content,
                sample: {
                  role: "subject",
                  sample_id: "chip",
                  revision: 2,
                  content_hash: `sha256:${"c".repeat(64)}`,
                  kind: "chip",
                  display_name: "Chip",
                },
                projection: [],
              },
            },
          },
        });
      }
      return fallback(request);
    }),
  );
  const view = render(<Harness />);
  await selectPrepared();
  fireEvent.click(screen.getByRole("button", { name: "Browse samples, batches and collections" }));
  const picker = await screen.findByLabelText("Registered target");
  await screen.findByRole("option", { name: /Chip target/ });
  fireEvent.change(picker, { target: { value: targetRefKey(ref) } });
  fireEvent.change(screen.getByLabelText("Operator"), { target: { value: "Alice" } });
  fireEvent.change(screen.getByLabelText("Experimental batch"), { target: { value: "batch-a" } });
  fireEvent.change(screen.getByLabelText("Record collection"), {
    target: { value: "collection-a" },
  });
  await previewReady();
  head = { ...target, name: "New target label", ref: { ...ref, revision: 2 } };
  fireEvent.click(screen.getByRole("button", { name: "Refresh target list" }));
  await screen.findByRole("option", { name: /New target label/ });
  expect(screen.getByText("Preview ready", { exact: true })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "configuration" }));
  await returnToLaunch();
  fireEvent.change(screen.getByLabelText("Experiment"), { target: { value: "first" } });
  await previewReady();
  expect(resolutions.length).toBeGreaterThan(0);
  expect(resolutions.every((value) => JSON.stringify(value) === JSON.stringify(ref))).toBe(true);
  expect(previews.at(-1)?.selection).toMatchObject({
    subject: { kind: "registered_target", ref },
    batch: { kind: "declared", id: "batch-a" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Start acquisition" }));
  await waitFor(() => expect(submissions).toHaveLength(1));
  expect(submissions[0]).toMatchObject({
    actor: "Alice",
    record_collection: "collection-a",
    selection: previews.at(-1)?.selection,
    reviewed: { binding: { subject: { kind: "registered_target", ref } } },
  });
  fireEvent.change(screen.getByLabelText("Experimental batch"), { target: { value: "batch-b" } });
  expect(screen.queryByText("Preview ready", { exact: true })).toBeNull();
  view.rerender(<Harness projectId="project-b" />);
  await selectPrepared();
  expect(screen.getByLabelText("Sample ID")).toHaveValue("");
  expect(screen.getByLabelText("Operator")).toHaveValue("operator");
  expect(screen.queryByText(/exact registered target retained/)).toBeNull();
});

function mockWorkspaceCatalog() {
  const fallback = globalThis.fetch;
  const catalogOwners: string[] = [];
  const previewOwners: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/author-workspaces"))
        return Response.json({
          items: [
            { id: "legacy", name: "Service code", available: true, unavailable_reason: null },
            { id: "workspace-b", name: "Second code", available: true, unavailable_reason: null },
            {
              id: "unbound",
              name: "Archived code",
              available: false,
              unavailable_reason: "Register its local source before execution",
            },
          ],
        });
      if (path.endsWith("/experiment-launcher"))
        catalogOwners.push(request.headers.get("X-Scopecat-Workspace") ?? "missing");
      if (path.endsWith("/preview")) {
        const body = (await request.clone().json()) as SubmissionRequest;
        previewOwners.push(body.workspace_id);
      }
      return fallback(request);
    }),
  );
  return { catalogOwners, previewOwners };
}

it("switches identical experiment definitions by workspace without replacing scientific context or an uncertain submission", async () => {
  const requests = mockWorkspaceCatalog();
  render(<Harness />);
  await selectPrepared();
  await screen.findByRole("option", { name: /Second code/ });
  expect(screen.getByRole("option", { name: /Archived code/ })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "service-only edit" } });
  fireEvent.change(screen.getByLabelText("Sample ID"), { target: { value: "chip-a" } });
  fireEvent.change(screen.getByLabelText("Operator"), { target: { value: "Alice" } });
  fireEvent.click(screen.getByRole("button", { name: "Browse samples, batches and collections" }));
  await screen.findByRole("option", { name: "Collection A" });
  fireEvent.change(screen.getByLabelText("Record collection"), {
    target: { value: "collection-a" },
  });
  await previewReady();
  fireEvent.click(screen.getByRole("button", { name: "Start acquisition" }));
  await waitFor(() => expect(submissions).toHaveLength(1));
  await screen.findByRole("button", { name: "Retry original submission" });
  const original = submissions[0];
  fireEvent.change(screen.getByLabelText("Code workspace"), { target: { value: "workspace-b" } });
  await selectPrepared();
  expect(screen.getByLabelText("Note")).toHaveValue("original");
  expect(screen.getByLabelText("Sample ID")).toHaveValue("chip-a");
  expect(screen.getByLabelText("Operator")).toHaveValue("Alice");
  expect(screen.getByLabelText("Record collection")).toHaveValue("collection-a");
  expect(screen.queryByText("Preview ready", { exact: true })).toBeNull();
  expect(screen.getByRole("button", { name: "Retry original submission" })).toBeDisabled();
  expect(submissions).toEqual([original]);
  fireEvent.click(screen.getByRole("button", { name: "devices" }));
  await returnToLaunch();
  expect(screen.getByLabelText("Code workspace")).toHaveValue("workspace-b");
  await previewReady();
  expect(requests.catalogOwners).toContain("workspace-b");
  expect(requests.previewOwners).toEqual(["legacy", "workspace-b"]);
  fireEvent.change(screen.getByLabelText("Code workspace"), { target: { value: "legacy" } });
  await selectPrepared();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Retry original submission" })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Retry original submission" }));
  await waitFor(() => expect(submissions).toHaveLength(2));
  expect(submissions[1]).toEqual(original);
});

it("ignores a pending preview from the previous source even when the experiment definition is identical", async () => {
  mockWorkspaceCatalog();
  const view = render(<Harness />);
  await selectPrepared();
  await screen.findByRole("option", { name: /Second code/ });
  deferPreview = true;
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  await waitFor(() => expect(previewResponse).toBeDefined());
  const previous = previewResponse!;
  fireEvent.change(screen.getByLabelText("Code workspace"), { target: { value: "workspace-b" } });
  await selectPrepared();
  await act(async () => {
    previous(Response.json(preview()));
  });
  expect(screen.queryByText("Preview ready", { exact: true })).toBeNull();
  expect(screen.getByRole("button", { name: "Start acquisition" })).toBeDisabled();
  expect(screen.getByLabelText("Code workspace")).toHaveValue("workspace-b");
  view.rerender(<Harness projectId="project-b" />);
  await selectPrepared();
  expect(screen.getByLabelText("Code workspace")).toHaveValue("legacy");
});

it("opens the linked source without loading the service owner's catalog", async () => {
  window.history.replaceState(null, "", "/?workspace=workspace-b");
  const { catalogOwners, previewOwners } = mockWorkspaceCatalog();
  render(<Harness />);
  await selectPrepared();
  expect(screen.getByLabelText("Code workspace")).toHaveValue("workspace-b");
  await previewReady();
  expect(catalogOwners.length).toBeGreaterThan(0);
  expect(catalogOwners.every((id) => id === "workspace-b")).toBe(true);
  expect(previewOwners).toEqual(["workspace-b"]);
});

it("keeps an unknown linked source unavailable instead of selecting default code", async () => {
  window.history.replaceState(null, "", "/?workspace=missing-source");
  const { catalogOwners } = mockWorkspaceCatalog();
  render(<Harness />);
  expect(await screen.findByText(/missing-source is not registered/)).toBeVisible();
  expect(screen.getByLabelText("Code workspace")).toHaveValue("missing-source");
  expect(catalogOwners).toEqual([]);
  expect(submissions).toEqual([]);
});
