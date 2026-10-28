// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { Activity, useEffect } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CandidateLaunch } from "./CandidateLaunch";
import { LaunchDraftProvider, useLaunchDraft } from "../launch/LaunchDraft";
import { navigate, useLocationUrl } from "../../lib/navigation";
import { installLaunchRecoveryRoutes } from "../../test/launch-recovery-fixture";
import type { LaunchCatalogEntry } from "../launch/launch-api";

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
const entry: LaunchCatalogEntry = {
  id: "signal",
  version: "1",
  title: "Signal",
  description: "",
  actions: ["preview", "submit"],
  kind: "diagnostic",
  configuration_effect: "none",
  review: null,
  request: { properties: {}, required: [] },
  controls: [],
};
let state: ReturnType<typeof useLaunchDraft>;
function Routes({ retained }: { retained: boolean }) {
  const editor = useLaunchDraft();
  const location = useLocationUrl();
  useEffect(() => {
    state = editor;
  }, [editor]);
  const card = (
    <CandidateLaunch
      key={location.searchParams.get("run")}
      proposal={{ ...proposal, sourceRunId: location.searchParams.get("run") ?? "baseline" }}
      projectId="application"
    />
  );
  return retained ? (
    <Activity mode={location.hash === "#runs" ? "visible" : "hidden"}>{card}</Activity>
  ) : location.hash === "#runs" ? (
    card
  ) : (
    <p>Other route</p>
  );
}
function mount(retained = false) {
  window.history.replaceState(null, "", "/?run=baseline&workspace=legacy#runs");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/launch-selection"))
        return Response.json({
          configuration: { kind: "candidate", source },
          subject: { kind: "unbound" },
          batch: { kind: "unscoped" },
        });
      if (path.endsWith("/author-workspaces"))
        return Response.json({ items: [{ id: "legacy", name: "Lab", available: true }] });
      if (path.endsWith("/experiment-launcher")) return Response.json({ entries: [entry] });
      throw new Error(path);
    }),
  );
  installLaunchRecoveryRoutes();
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <LaunchDraftProvider projectId="application">
        <Routes retained={retained} />
      </LaunchDraftProvider>
    </QueryClientProvider>,
  );
}
async function choose() {
  fireEvent.click(screen.getByText("用于新实验"));
  await screen.findByRole("option", { name: "Signal" });
  fireEvent.change(screen.getByLabelText("实验类型"), { target: { value: "signal" } });
  await waitFor(() => expect(screen.getByText("创建候选草稿")).toBeEnabled());
}
afterEach(async () => {
  cleanup();
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 300));
  });
  vi.unstubAllGlobals();
});
it.each(["unmount", "activity", "activity-settled-hidden", "other-run"])(
  "revokes a delayed save after leaving and returning via %s",
  async (mode) => {
    mount(mode !== "unmount");
    await choose();
    const transport = globalThis.fetch;
    let release!: () => void;
    let savedResponse: Response | undefined;
    let saveCount = 0;
    vi.stubGlobal("fetch", async (request: Request) => {
      const response = await transport(request);
      if (request.url.endsWith("/launch-drafts/save")) {
        saveCount++;
        savedResponse = response.clone();
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      }
      return response;
    });
    const origin = window.location.href;
    fireEvent.click(screen.getByText("创建候选草稿"));
    await waitFor(() => expect(saveCount).toBe(1));
    fireEvent.click(screen.getByText("正在保存候选草稿…"));
    expect(saveCount).toBe(1);
    act(() => navigate(mode === "other-run" ? "/?run=other&workspace=legacy#runs" : "#settings"));
    expect(Boolean(screen.queryByText("用于新实验"))).toBe(mode !== "unmount");
    if (mode === "activity-settled-hidden") {
      await act(async () => {
        release();
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    }
    expect(state.draft).toBeUndefined();
    await act(async () => {
      const returned = new Promise<void>((resolve) =>
        window.addEventListener("popstate", () => resolve(), { once: true }),
      );
      window.history.back();
      await returned;
    });
    if (!mode.startsWith("activity")) await choose();
    await act(async () => {
      release();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(state.draft).toBeUndefined();
    expect(window.location.href).toBe(origin);
    expect(screen.getByText("创建候选草稿")).toBeEnabled();
    expect(saveCount).toBe(1);
    const saved = (await savedResponse!.json()).saved;
    expect(saved.input.selection.configuration.source).toEqual(source);
  },
);
it("retries a lost response with the same save operation and then opens the candidate draft", async () => {
  mount();
  await choose();
  const transport = globalThis.fetch;
  const commands: unknown[] = [];
  let committed: Response | undefined;
  vi.stubGlobal("fetch", async (request: Request) => {
    if (request.url.endsWith("/launch-drafts/save")) {
      commands.push(await request.clone().json());
      if (!committed) {
        committed = await transport(request);
        throw new Error("response lost");
      }
      return committed.clone();
    }
    return transport(request);
  });
  fireEvent.click(screen.getByText("创建候选草稿"));
  await screen.findByRole("alert");
  expect(state.draft).toBeUndefined();
  fireEvent.click(screen.getByText("创建候选草稿"));
  await waitFor(() => expect(window.location.hash).toBe("#launch"));
  expect(commands).toHaveLength(2);
  expect(commands[1]).toEqual(commands[0]);
  expect(state.draft?.selection.configuration).toEqual({ kind: "candidate", source });
});
