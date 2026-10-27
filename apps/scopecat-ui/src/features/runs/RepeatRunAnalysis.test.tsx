// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { RunAnalysis } from "../../types";
import { RepeatRunAnalysis, repeatRequest } from "./RepeatRunAnalysis";

const source = "sha256:" + "a".repeat(64);
const request = {
  run_id: "retained-run",
  workspace_id: "original-workspace",
  code_revision: { content_hash: source },
  analysis: "signal.analysis:peak",
  arguments: { minimum: 0.2, tolerance: { value: 50, unit: "MHz" } },
  key: "peak",
  grouping: null,
  measurement_slice: null,
};
const analysis: RunAnalysis = {
  id: "old-analysis",
  title: "Peak",
  revision: 1,
  publicationHash: "publication-hash",
  publishedAt: "2026-10-10T00:00:00Z",
  subject: "run",
  inputs: [],
  executions: [],
  outputs: [
    {
      id: "author_analysis_request",
      title: "Saved analysis settings",
      kind: "artifact",
      metadata: {},
      content: {
        artifact_id: "settings",
        content_hash: "settings-hash",
        filename: "settings.txt",
        media_type: "text/plain",
      },
    },
    ...[
      ["author_workspace", "original-workspace"],
      ["author_code_revision", source],
    ].map(([id, value]) => ({
      id: id!,
      title: id!,
      kind: "fact" as const,
      metadata: {},
      content: {
        codec: "scopecat.analysis-fact.v1",
        schema_id: "text",
        schema_hash: "schema-hash",
        schema_codec: "scopecat.analysis-fact-schema.v1" as const,
        value,
      },
    })),
  ],
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function show(value = analysis) {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <RepeatRunAnalysis runId="retained-run" analysis={value} />
    </QueryClientProvider>,
  );
}
it("executes only an explicit repeat with the saved source and settings, then links the receipt", async () => {
  const posted: unknown[] = [];
  const fetch = vi.fn(async (input: Request) => {
    if (input.method === "GET")
      return Response.json({
        artifact: { content_hash: "settings-hash" },
        content: JSON.stringify(request),
      });
    posted.push(await input.json());
    return Response.json({ code_revision: request.code_revision, analysis_id: "new-analysis" });
  });
  vi.stubGlobal("fetch", fetch);
  show();
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "按已保存设置重新分析" }));
  expect(await screen.findByRole("link", { name: "查看已保存的结果" })).toHaveAttribute(
    "href",
    "?run=retained-run&run-analysis=new-analysis#runs",
  );
  expect(posted).toEqual([{ ...request, grouping: undefined, measurement_slice: undefined }]);
});
it("does not invent a repeat action for a publication without a saved invocation", () => {
  show({ ...analysis, outputs: [] });
  expect(screen.queryByRole("button")).toBeNull();
});
it.each([
  { run_id: "other-run" },
  { workspace_id: "other-workspace" },
  { code_revision: { content_hash: "other-source" } },
  { grouping: {} },
  { measurement_slice: "slice" },
  { analysis: "display-name" },
])("rejects settings outside this saved whole-run publication: %j", (change) => {
  expect(() =>
    repeatRequest(JSON.stringify({ ...request, ...change }), "retained-run", analysis),
  ).toThrow("匹配的整次运行分析设置");
});
it("does not submit changed artifact content or automatically retry a lost response", async () => {
  const fetch = vi.fn(async () =>
    Response.json({ artifact: { content_hash: "changed" }, content: JSON.stringify(request) }),
  );
  vi.stubGlobal("fetch", fetch);
  show();
  fireEvent.click(screen.getByRole("button", { name: "按已保存设置重新分析" }));
  await screen.findByRole("alert");
  expect(fetch).toHaveBeenCalledTimes(1);
  fetch.mockRejectedValue(new TypeError("lost response"));
  fireEvent.click(screen.getByRole("button", { name: "按已保存设置重新分析" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("未自动提交重试"));
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("keeps an uncertain execution explicit and never retries its POST", async () => {
  const fetch = vi.fn(async (input: Request) => {
    if (input.method === "GET")
      return Response.json({
        artifact: { content_hash: "settings-hash" },
        content: JSON.stringify(request),
      });
    throw new TypeError("lost POST response");
  });
  vi.stubGlobal("fetch", fetch);
  show();
  fireEvent.click(screen.getByRole("button", { name: "按已保存设置重新分析" }));
  await screen.findByRole("alert");
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("alert")).toHaveTextContent("请先检查分析历史");
  expect(screen.queryByRole("link", { name: "查看已保存的结果" })).toBeNull();
});
