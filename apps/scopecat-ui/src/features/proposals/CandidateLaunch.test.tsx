// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CandidateLaunch } from "./CandidateLaunch";
import { useOptionalLaunchDraft } from "../launch/LaunchDraft";
import { navigate } from "../../lib/navigation";
vi.mock("../launch/LaunchDraft", () => ({ useOptionalLaunchDraft: vi.fn() }));
vi.mock("../../lib/navigation", () => ({ navigate: vi.fn(), navigateLink: vi.fn() }));
const proposal = {
  id: "candidate",
  sourceRunId: "baseline",
  analysisRecordId: "fit",
  baseConfigId: "base",
  baseContentHash: "sha256:base",
  reason: "fit",
  evidenceOutputIds: [],
  deltas: [],
};
const source = {
  kind: "analysis_candidate",
  source_run_id: "baseline",
  proposal_id: "candidate",
  analysis_record_id: "fit",
  base_config_content_hash: "sha256:base",
  content_hash: "sha256:exact",
  setup: { revision_id: "original", content_hash: "sha256:setup" },
};
const selection = {
  configuration: { kind: "candidate", source },
  subject: { kind: "sample", sample_id: "sample", revision: 3 },
  batch: { kind: "declared", id: "batch" },
};
const entry = { id: "signal", title: "Signal", description: "" };
const copy = vi.fn();
let paths: string[];
let reply: unknown;
beforeEach(() => {
  vi.resetAllMocks();
  paths = [];
  reply = selection;
  copy.mockResolvedValue({
    selected: true,
    target: { workspace_id: "lab", experiment: "signal", draft_id: "new" },
  });
  vi.mocked(useOptionalLaunchDraft).mockReturnValue({
    workspaceId: "lab",
    recovery: { ready: true, writing: false },
    copyHistoricalConfiguration: copy,
  } as unknown as NonNullable<ReturnType<typeof useOptionalLaunchDraft>>);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      paths.push(path);
      if (path.endsWith("/launch-selection")) return Response.json(reply);
      if (path.endsWith("/author-workspaces"))
        return Response.json({ items: [{ id: "lab", name: "Lab", available: true }] });
      if (path.endsWith("/experiment-launcher")) return Response.json({ entries: [entry] });
      throw new Error(path);
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function mount() {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <CandidateLaunch proposal={proposal} projectId="project" />
    </QueryClientProvider>,
  );
}
async function choose() {
  fireEvent.click(screen.getByText("用于新实验"));
  await screen.findByRole("option", { name: "Signal" });
  fireEvent.change(screen.getByLabelText("实验类型"), { target: { value: "signal" } });
  await waitFor(() => expect(screen.getByText("创建候选草稿")).toBeEnabled());
}
it("cancels without saving or replacing the current editor", async () => {
  mount();
  await choose();
  fireEvent.click(screen.getByText("取消返回"));
  expect(copy).not.toHaveBeenCalled();
  expect(navigate).not.toHaveBeenCalled();
  expect(paths.every((path) => !path.includes("submit") && !path.includes("publication"))).toBe(
    true,
  );
});
it("copies the selected experiment with exact candidate context only once", async () => {
  let finish!: (value: unknown) => void;
  copy.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  mount();
  await choose();
  fireEvent.click(screen.getByText("创建候选草稿"));
  fireEvent.click(screen.getByText("正在保存候选草稿…"));
  expect(copy).toHaveBeenCalledTimes(1);
  expect(copy).toHaveBeenCalledWith("baseline", source, {
    entry,
    workspaceId: "lab",
    selection,
    codeRevision: undefined,
    isIntended: expect.any(Function),
  });
  finish({
    selected: true,
    target: { workspace_id: "lab", experiment: "signal", draft_id: "new" },
  });
  await waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));
});
it.each(["source_run_id", "proposal_id", "analysis_record_id", "base_config_content_hash"])(
  "rejects a mismatched %s without substituting parameters",
  async (field) => {
    reply = {
      ...selection,
      configuration: { kind: "candidate", source: { ...source, [field]: "wrong" } },
    };
    mount();
    fireEvent.click(screen.getByText("用于新实验"));
    expect(await screen.findByRole("alert")).toHaveTextContent("候选来源与当前卡片不一致");
    expect(screen.getByText("创建候选草稿")).toBeDisabled();
    expect(copy).not.toHaveBeenCalled();
  },
);
it("retains a link instead of navigating when the user leaves during save", async () => {
  copy.mockImplementation(async () => {
    window.history.replaceState(null, "", "/?run=other#runs");
    return {
      selected: false,
      target: { workspace_id: "lab", experiment: "signal", draft_id: "new" },
    };
  });
  mount();
  await choose();
  fireEvent.click(screen.getByText("创建候选草稿"));
  expect(await screen.findByText("打开候选草稿")).toHaveAttribute(
    "href",
    expect.stringContaining("draft=new"),
  );
  expect(navigate).not.toHaveBeenCalled();
});

it("keeps an unavailable candidate explicit and does not create a default draft", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ detail: "Candidate unavailable" }, { status: 409 })),
  );
  mount();
  fireEvent.click(screen.getByText("用于新实验"));
  await screen.findAllByRole("alert");
  expect(screen.getByText("创建候选草稿")).toBeDisabled();
  expect(copy).not.toHaveBeenCalled();
});
