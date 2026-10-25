// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { AnalysisPublication } from "../../types";
import { readOnlyCode } from "../../lib/read-only-code";
import { AnalysisPublicationView } from "./AnalysisPublicationView";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const analysis: AnalysisPublication = {
  id: "analysis-fit-r1",
  key: "fit",
  title: "Old saved result",
  revision: 1,
  publicationHash: "hash",
  publishedAt: "2026-09-08T00:00:00Z",
  subject: "run",
  inputs: [
    {
      id: "other",
      kind: "measurement_dataset",
      codec: "scopecat.measurement.v1",
      run_id: "input-not-owner",
      target: "data",
      role: "input",
      content_hash: "hash",
    },
  ],
  outputs: [],
  executions: [],
};

it("requires an explicit owner even when inputs mention a run", () => {
  render(<AnalysisPublicationView analysis={analysis} getArtifactDownload={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "Copy read-only code" })).toBeNull();
});

it("copies the supplied owner and exact publication rather than its input or logical key", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, get: () => ({ writeText }) });
  const { rerender } = render(
    <AnalysisPublicationView
      analysis={analysis}
      copyTarget={{ kind: "run", runId: "owner" }}
      getArtifactDownload={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Copy read-only code" }));
  await screen.findByText("Read-only code copied.");
  expect(writeText).toHaveBeenLastCalledWith(
    readOnlyCode({ kind: "run", runId: "owner", publicationId: analysis.id }),
  );
  rerender(
    <AnalysisPublicationView
      analysis={{ ...analysis, subject: "project" }}
      copyTarget={{ kind: "project" }}
      getArtifactDownload={vi.fn()}
    />,
  );
  expect(screen.queryByText("Read-only code copied.")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Copy read-only code" }));
  await screen.findByText("Read-only code copied.");
  expect(writeText).toHaveBeenLastCalledWith(
    readOnlyCode({ kind: "project", publicationId: analysis.id }),
  );
});

it("keeps saved output and units visible while exact input evidence is collapsed", () => {
  const openRun = vi.fn();
  render(
    <AnalysisPublicationView
      analysis={{
        ...analysis,
        outputs: [
          {
            id: "fit",
            kind: "table",
            title: "Saved fit",
            metadata: {},
            content: {
              source: { kind: "dataset", output_id: "fits" },
              columns: ["frequency"],
              total_rows: 2,
              truncated: true,
              preview: {
                columns: [{ id: "frequency", label: "Frequency", unit: "GHz" }],
                rows: [{ cells: [4.8] }],
              },
            },
          },
        ],
      }}
      getArtifactDownload={vi.fn()}
      onOpenRun={openRun}
    />,
  );
  expect(screen.getByRole("table", { name: "Saved fit" })).toBeVisible();
  expect(screen.getByRole("columnheader", { name: "Frequency (GHz)" })).toBeVisible();
  expect(screen.getByText("Showing 1 of 2 rows")).toBeVisible();
  expect(screen.getByText("Inputs")).not.toBeVisible();
  fireEvent.click(screen.getByText("来源与执行证据"));
  expect(screen.getByText("Inputs")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "input-not-owner" }));
  expect(openRun).toHaveBeenCalledWith("input-not-owner");
  fireEvent.click(screen.getByText("来源与执行证据"));
  expect(screen.getByRole("table", { name: "Saved fit" })).toBeVisible();
});
