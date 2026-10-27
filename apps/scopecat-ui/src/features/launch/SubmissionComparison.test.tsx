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
it("compares selected receipt A with named draft B without requests or preparing 其他 run", () => {
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
  fireEvent.click(screen.getByText("提交来源与请求详情"));
  expect(screen.getByText(request.request_key)).toBeVisible();
  fireEvent.click(screen.getByText("提交来源与请求详情"));
  expect(screen.getByText(request.request_key)).not.toBeVisible();
  fireEvent.click(screen.getByText("比较已提交请求与当前草稿"));
  expect(screen.getByRole("columnheader", { name: "当前草稿 · Draft B" })).toBeVisible();
  expect(screen.getByText(/这不表示当前草稿就是最初提交的草稿/)).toBeVisible();
  expect(screen.getByText("输入 · count")).toBeVisible();
  expect(screen.queryByText("输入 · enabled")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "显示未变化字段" }));
  expect(screen.getByText("输入 · enabled")).toBeVisible();
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
  fireEvent.click(screen.getByText("比较已提交请求与当前草稿"));
  expect(screen.getByText(/当前选择了不同的工作区或实验/)).toBeVisible();
  expect(screen.getByText("workspace-c")).toBeVisible();
});

it("keeps submitted inputs readable before a draft is selected", () => {
  const { request } = comparisonFixture();
  render(<SubmissionComparison request={request} />);
  fireEvent.click(screen.getByText("已提交输入"));
  expect(screen.getByText("输入 · count")).toBeVisible();
  expect(screen.getByText("固定值 · 1000 Hz")).toBeVisible();
  expect(screen.getByText(/选择草稿以与此已提交请求比较/)).toBeVisible();
});
