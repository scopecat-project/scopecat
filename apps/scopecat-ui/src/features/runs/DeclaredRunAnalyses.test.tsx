// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DeclaredRunAnalyses, type RunAnalysisActions } from "./DeclaredRunAnalyses";

const catalog: RunAnalysisActions = {
  run_id: "original-run",
  workspace_id: "original-workspace",
  code_revision: { content_hash: "sha256:" + "a".repeat(64) },
  experiment: "signal",
  actions: [
    {
      title: "Check expected peak",
      analysis: "signal.analysis:verify_peak",
      description: "Check retained data against an explicit expected peak.",
      inputs: [
        { name: "expected_frequency", kind: "quantity", choices: [], required: true },
        {
          name: "tolerance",
          kind: "quantity",
          choices: [],
          required: false,
          default: { value: 50, unit: "MHz" },
        },
      ],
    },
  ],
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function show(value = catalog) {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <DeclaredRunAnalyses catalog={value} />
    </QueryClientProvider>,
  );
}
it("requires explicit input and execution, keeps units/source, and opens the returned publication", async () => {
  const bodies: unknown[] = [];
  const fetch = vi.fn(async (request: Request) => {
    bodies.push(await request.json());
    return Response.json({
      code_revision: catalog.code_revision,
      analysis_id: "verification-result",
    });
  });
  vi.stubGlobal("fetch", fetch);
  show();
  expect(fetch).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Expected Frequency")).toBeRequired();
  expect(screen.getByLabelText("Tolerance")).toHaveValue(50);
  expect(screen.getByLabelText("Tolerance unit")).toHaveValue("MHz");
  fireEvent.change(screen.getByLabelText("Expected Frequency"), { target: { value: "4.8" } });
  fireEvent.change(screen.getByLabelText("Expected Frequency unit"), { target: { value: "GHz" } });
  fireEvent.click(screen.getByRole("button", { name: "Run Check expected peak" }));
  expect(await screen.findByRole("link", { name: "View saved result" })).toHaveAttribute(
    "href",
    "?run=original-run&run-analysis=verification-result#runs",
  );
  expect(bodies).toEqual([
    {
      run_id: catalog.run_id,
      workspace_id: catalog.workspace_id,
      code_revision: catalog.code_revision,
      analysis: "signal.analysis:verify_peak",
      arguments: {
        expected_frequency: { value: 4.8, unit: "GHz" },
        tolerance: { value: 50, unit: "MHz" },
      },
    },
  ]);
  fireEvent.change(screen.getByLabelText("Tolerance"), { target: { value: "25" } });
  expect(screen.queryByRole("link", { name: "View saved result" })).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("does not offer a guessed action when the retained declaration has none", () => {
  show({ ...catalog, actions: [] });
  expect(screen.queryByRole("button")).toBeNull();
});
it("explains unsupported inputs and makes no execute button", () => {
  show({
    ...catalog,
    actions: [{ ...catalog.actions[0]!, unavailable_reason: "Use Python for this input type." }],
  });
  expect(screen.getByText("Use Python for this input type.")).toBeInTheDocument();
  expect(screen.queryByRole("button")).toBeNull();
});
it("does not automatically retry a lost analysis response", async () => {
  const fetch = vi.fn(async () => {
    throw new TypeError("connection lost");
  });
  vi.stubGlobal("fetch", fetch);
  show({ ...catalog, actions: [{ ...catalog.actions[0]!, inputs: [] }] });
  fireEvent.click(screen.getByRole("button", { name: "Run Check expected peak" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Check analysis history");
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("link", { name: "View saved result" })).toBeNull();
});
it("preserves an explicitly empty string instead of silently restoring its default", async () => {
  const bodies: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      bodies.push(await request.json());
      return Response.json({ code_revision: catalog.code_revision, analysis_id: "empty-label" });
    }),
  );
  show({
    ...catalog,
    actions: [
      {
        ...catalog.actions[0]!,
        inputs: [
          { name: "label", kind: "text", required: false, default: "original", choices: [] },
        ],
      },
    ],
  });
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Run Check expected peak" }));
  await screen.findByRole("link", { name: "View saved result" });
  expect(bodies[0]).toMatchObject({ arguments: { label: "" } });
});
