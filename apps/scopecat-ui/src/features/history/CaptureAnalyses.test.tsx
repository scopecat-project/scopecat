// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CaptureAnalyses } from "./CaptureAnalyses";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("saves an artifact from its captured analysis with cancellation and failure feedback", async () => {
  const save = vi
    .fn()
    .mockResolvedValueOnce("/chosen/report.txt")
    .mockResolvedValueOnce(null)
    .mockRejectedValueOnce(new Error("Disk full"));
  vi.stubGlobal("pywebview", { api: { save_captured_artifact: save } });
  render(
    <CaptureAnalyses
      contentHash="capture"
      onOpenRun={vi.fn()}
      analyses={[
        {
          entry: {
            role: "record",
            id: "analysis",
            kind: "analysis",
            content_hash: "sha256:record",
          },
          published_at: "2026-10-02T00:00:00Z",
          contents: [],
          record: {
            subject: { kind: "run", run_id: "result" },
            title: "Report",
            revision: 1,
            publication_hash: "sha256:publication",
            outputs: [
              {
                kind: "artifact",
                id: "report",
                title: "Report file",
                content: {
                  artifact_id: "artifact",
                  content_hash: "sha256:artifact",
                  filename: "report.txt",
                  media_type: "text/plain",
                },
              },
            ],
          },
        },
      ]}
    />,
  );
  fireEvent.click(screen.getByText("Report · revision 1"));
  const button = screen.getByRole("button", { name: "Download file" });
  fireEvent.click(button);
  await screen.findByText("Saved to /chosen/report.txt");
  expect(save).toHaveBeenCalledWith("capture", "sha256:record", "artifact", "report.txt");
  fireEvent.click(button);
  await waitFor(() => expect(button).toHaveProperty("disabled", false));
  expect(screen.queryByRole("status")).toBeNull();
  fireEvent.click(button);
  expect((await screen.findByRole("alert")).textContent).toBe("Disk full");
  expect(button).toHaveProperty("disabled", false);
});

it("uses retained analysis inputs for navigation and displays published facts", () => {
  const onOpenRun = vi.fn();
  render(
    <CaptureAnalyses
      contentHash="capture"
      onOpenRun={onOpenRun}
      analyses={[
        {
          entry: {
            role: "record",
            id: "analysis",
            kind: "analysis",
            content_hash: "sha256:record",
          },
          published_at: "2026-10-02T00:00:00Z",
          contents: [],
          record: {
            subject: { kind: "run", run_id: "result" },
            title: "Fit",
            revision: 1,
            publication_hash: "sha256:publication",
            inputs: [
              {
                id: "measurements",
                kind: "measurement_dataset",
                run_id: "source",
                target: "datasets/raw-measurements",
                content_hash: "sha256:data",
                codec: "scopecat.measurements.arrow.v1",
                role: "source",
              },
            ],
            outputs: [
              {
                kind: "fact",
                id: "frequency",
                title: "Fitted frequency",
                content: {
                  schema_id: "fit.v1",
                  schema_codec: "scopecat.analysis-fact-schema.v1",
                  schema_hash: "sha256:schema",
                  codec: "scopecat.python-json.v1",
                  value: { frequency: 5.1 },
                },
              },
            ],
          },
        },
      ]}
    />,
  );
  fireEvent.click(screen.getByText("Fit · revision 1"));
  expect(screen.getByText("Fitted frequency")).toBeTruthy();
  expect(screen.getByText("fit.v1")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "source" }));
  expect(onOpenRun).toHaveBeenCalledWith("source");
  expect(screen.queryByRole("button", { name: /activate|apply/i })).toBeNull();
});
