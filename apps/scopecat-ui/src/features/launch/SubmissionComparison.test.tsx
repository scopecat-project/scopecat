// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SubmissionComparison } from "./SubmissionComparison";
import { OriginalSubmission } from "./OriginalSubmission";
import { comparisonFixture } from "./submission-comparison.test-support";
import { useLaunchDraft } from "./LaunchDraft";

vi.mock("./LaunchDraft", () => ({ useLaunchDraft: vi.fn() }));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("compares selected receipt A with named draft B without requests or preparing another run", () => {
  const { request, draft } = comparisonFixture();
  draft.values.count = "3";
  const fetch = vi.fn();
  const rerun = vi.fn();
  const check = vi.fn();
  vi.stubGlobal("fetch", fetch);
  vi.mocked(useLaunchDraft, { partial: true }).mockReturnValue({
    attempt: {
      request,
      status: "confirmed",
      definition: "definition",
      error: "",
      procedureId: "a",
    },
    draft,
    rerun,
    checkSubmission: check,
  });
  render(<OriginalSubmission onOpen={vi.fn()} catalogReady />);
  expect(screen.getByText(request.request_key)).not.toBeVisible();
  fireEvent.click(screen.getByText("Submission source and request details"));
  expect(screen.getByText(request.request_key)).toBeVisible();
  fireEvent.click(screen.getByText("Submission source and request details"));
  expect(screen.getByText(request.request_key)).not.toBeVisible();
  fireEvent.click(screen.getByText("Compare submitted request with current draft"));
  expect(screen.getByRole("columnheader", { name: "Current draft · Draft B" })).toBeVisible();
  expect(screen.getByText(/does not identify the draft originally submitted/)).toBeVisible();
  expect(screen.getByText("Input · count")).toBeVisible();
  expect(screen.queryByText("Input · enabled")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Show unchanged fields" }));
  expect(screen.getByText("Input · enabled")).toBeVisible();
  expect(fetch).not.toHaveBeenCalled();
  expect(rerun).not.toHaveBeenCalled();
  expect(check).not.toHaveBeenCalled();
  expect(request.inputs?.count).toBe(2);
});
it("explicitly identifies comparisons across workspace or experiment", () => {
  const { request, draft } = comparisonFixture();
  draft.workspaceId = "workspace-c";
  draft.experiment = "other";
  render(<SubmissionComparison request={request} draft={draft} />);
  fireEvent.click(screen.getByText("Compare submitted request with current draft"));
  expect(screen.getByText(/Different workspace or experiment selected/)).toBeVisible();
  expect(screen.getByText("workspace-c")).toBeVisible();
});

it("keeps submitted inputs readable before a draft is selected", () => {
  const { request } = comparisonFixture();
  render(<SubmissionComparison request={request} />);
  fireEvent.click(screen.getByText("Submitted inputs"));
  expect(screen.getByText("Input · count")).toBeVisible();
  expect(screen.getByText("Fixed · 1000 Hz")).toBeVisible();
  expect(screen.getByText(/Select a draft to compare/)).toBeVisible();
});
