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
