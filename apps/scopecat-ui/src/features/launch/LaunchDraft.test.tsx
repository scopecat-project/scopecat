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
let generation: number;
let configFails: boolean;
let rejectSubmission: boolean;
let submissions: Record<string, unknown>[];
let previewResponse: ((response: Response) => void) | undefined;
let deferPreview: boolean;
let client: QueryClient;
let lookupMatch: "none" | "original" | "ambiguous" | "unverified" | "different-config";
function preview() {
  return {
    experiment_id: "prepared",
    request_hash: `sha256:${"a".repeat(64)}`,
    point_count: 1,
    config_source: {
      kind: "config_registry",
      selector: "active",
      entry_id: "baseline",
      config_ref: "baseline",
      content_hash: `sha256:${"b".repeat(64)}`,
      registry_generation: generation,
    },
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
  lookupMatch = "none";
  generation = 1;
  configFails = false;
  rejectSubmission = false;
  submissions = [];
  previewResponse = undefined;
  deferPreview = false;
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      if (path.endsWith("/procedures") && url.searchParams.has("request_key")) {
        const original = submissions[0];
        const item = {
          procedure_run_id: "original-procedure",
          request_key: original?.request_key,
          definition: { id: "maintained.launch_prepared", version: "1" },
          intent: {
            request_hash: lookupMatch === "unverified" ? "other" : original?.expected_request_hash,
            config_source:
              lookupMatch === "different-config"
                ? {
                    ...(original?.config_source as Record<string, unknown>),
                    registry_generation: 999,
                  }
                : original?.config_source,
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
      if (path.endsWith("/experiment-launcher")) return Response.json({ entries: catalog });
      if (path.endsWith("/config-registry")) {
        if (configFails) throw new TypeError("temporarily offline");
        return Response.json({ entries: [], activation: { entry_id: "baseline", generation } });
      }
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
  expect(screen.getByLabelText("Sample ID")).toHaveValue("");
  expect(screen.getByLabelText("Operator")).toHaveValue("operator");
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
