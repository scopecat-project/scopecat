import { readOperatorSettings } from "../application/OperatorSettings";
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { getHealth } from "../../data/project-api";
import { ApiError } from "../../api-client";
import { CandidateAdoption } from "./CandidateAdoption";
import * as api from "./candidate-adoption-api";
vi.mock("../../data/project-api", () => ({ getHealth: vi.fn() }));
vi.mock("./candidate-adoption-api", () => ({
  branches: vi.fn(),
  history: vi.fn(),
  revision: vi.fn(),
  sourceRun: vi.fn(),
  workingTable: vi.fn(),
  verifications: vi.fn(),
  publish: vi.fn(),
}));
const base = { revision_id: "base", content_hash: "sha256:base" };
const head: api.Branch = {
  name: "daily",
  generation: 4,
  revision: base,
  actor: "author",
  note: "",
  recorded_at: "2026-10-12T00:00:00Z",
};
const reference = {
  analysis_record_id: "verified",
  output_id: "decision",
  schema_id: "policy",
  schema_hash: "sha256:policy",
};
const proposal = {
  id: "carrier",
  sourceRunId: "run",
  analysisRecordId: "fit",
  baseConfigId: "base",
  baseContentHash: "sha256:base",
  reason: "fit",
  evidenceOutputIds: [],
  proposedAt: "2026-10-12T00:00:00Z",
  deltas: [{ parameterId: "frequency", before: 5, after: 5.1 }],
};
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 1, operator: "local-operator" });
  sessionStorage.clear();
  vi.mocked(getHealth).mockResolvedValue({
    status: "ok",
    projectId: "space-a",
    projectName: "A",
    projectRoot: "/a",
    details: {},
  });
  vi.mocked(api.branches).mockResolvedValue({ items: [head] });
  vi.mocked(api.history).mockResolvedValue({ items: [head] });
  vi.mocked(api.sourceRun).mockResolvedValue({
    snapshot: { config_source: { kind: "parameter_revision", parameters: base, overrides: [] } },
  } as unknown as Awaited<ReturnType<typeof api.sourceRun>>);
  vi.mocked(api.revision).mockResolvedValue({
    parameters: { values: [{ id: "frequency", shape: "scalar", value: 5 }] },
  } as Awaited<ReturnType<typeof api.revision>>);
  vi.mocked(api.workingTable).mockResolvedValue(null);
  vi.mocked(api.verifications).mockResolvedValue({
    items: [{ reference, title: "Verified candidate" }],
  });
});
afterEach(cleanup);
function mount(projectId = "space-a", currentProposal = proposal) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const content = (id: string) => (
    <QueryClientProvider client={client}>
      <CandidateAdoption proposal={currentProposal} projectId={id} />
    </QueryClientProvider>
  );
  const view = render(content(projectId));
  return { ...view, switchProject: (id: string) => view.rerender(content(id)) };
}
async function select() {
  fireEvent.click(screen.getByText("Review adoption and records"));
  await screen.findByRole("option", { name: /daily/ });
  fireEvent.change(screen.getByLabelText("Target parameter branch"), {
    target: { value: "daily" },
  });
  await screen.findByText("Matches original");
  fireEvent.change(screen.getByLabelText("Independent verification"), {
    target: { value: "verified/decision" },
  });
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Adopt verified candidate into daily" }),
    ).toBeEnabled(),
  );
}
it("retains the original request across unmount and rejects an unverified response", async () => {
  vi.mocked(api.publish).mockRejectedValueOnce(new ApiError("lost response"));
  const first = mount();
  await select();
  fireEvent.click(screen.getByText("Adopt verified candidate into daily"));
  await screen.findByText("lost response");
  const command = vi.mocked(api.publish).mock.calls[0]![0];
  expect(command).toMatchObject({
    name: "daily",
    expected_generation: 4,
    base,
    verification: reference,
  });
  first.unmount();
  mount();
  fireEvent.click(screen.getByText("Review adoption and records"));
  vi.mocked(api.publish).mockResolvedValueOnce({} as api.Branch);
  fireEvent.click(await screen.findByText("Recover original adoption"));
  await screen.findByText(
    "The adoption response could not be verified. Recover the original request.",
  );
  expect(api.publish).toHaveBeenNthCalledWith(2, command, "space-a");
  vi.mocked(api.publish).mockResolvedValueOnce({
    ...head,
    generation: 5,
    revision: { ...base, revision_id: command.revision_id },
    publication: { run_id: "run", proposal_id: "carrier", verification: reference },
  });
  fireEvent.click(screen.getByText("Recover original adoption"));
  await screen.findByRole("status");
  expect(api.publish).toHaveBeenNthCalledWith(3, command, "space-a");
  expect(sessionStorage.length).toBe(0);
});
it("keeps the reviewed generation fixed while refreshing current branch conflicts", async () => {
  mount();
  await select();
  vi.mocked(api.history).mockResolvedValue({
    items: [{ ...head, generation: 5, revision: { ...base, revision_id: "edited" } }, head],
  });
  vi.mocked(api.revision).mockResolvedValue({
    parameters: { values: [{ id: "frequency", shape: "scalar", value: 6 }] },
  } as Awaited<ReturnType<typeof api.revision>>);
  fireEvent.click(screen.getByText("Refresh evidence and records"));
  await screen.findByText("Changed since candidate");
  expect(screen.getByText(/Reviewed branch: daily · generation 4/)).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Adopt verified candidate into daily" }),
  ).toBeDisabled();
  expect(api.publish).not.toHaveBeenCalled();
});
it("keeps ineligible verification unavailable and reads only the selected branch draft", async () => {
  vi.mocked(api.verifications).mockResolvedValue({
    items: [
      {
        reference,
        title: "Old data",
        unavailable_reason: "Independent candidate acquisition required",
      },
    ],
  });
  mount();
  fireEvent.click(screen.getByText("Review adoption and records"));
  await screen.findByRole("option", { name: /daily/ });
  fireEvent.change(screen.getByLabelText("Target parameter branch"), {
    target: { value: "daily" },
  });
  expect(await screen.findByText("Independent candidate acquisition required")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Adopt verified candidate into daily" }),
  ).toBeDisabled();
  expect(api.workingTable).toHaveBeenCalledWith("daily");
  expect(api.workingTable).toHaveBeenCalledTimes(1);
  expect(api.publish).not.toHaveBeenCalled();
});
it("shows raw working edits separately without adding them to publication", async () => {
  vi.mocked(api.workingTable).mockResolvedValue({
    draft: {
      draft_id: "draft",
      input: { values: [{ id: "frequency", shape: "scalar", value: { text: "1e", unit: "GHz" } }] },
    },
    head_revision: 8,
  } as Awaited<ReturnType<typeof api.workingTable>>);
  vi.mocked(api.publish).mockRejectedValue(new ApiError("conflict", 409));
  mount();
  await select();
  expect(
    screen.getByRole("columnheader", { name: "Working input (raw, not adopted)" }),
  ).toBeVisible();
  expect(screen.getByRole("cell", { name: '{"text":"1e","unit":"GHz"}' })).toBeVisible();
  fireEvent.click(screen.getByText("Adopt verified candidate into daily"));
  await screen.findByText("conflict");
  expect(api.publish).toHaveBeenCalledTimes(1);
  expect(api.publish).toHaveBeenCalledWith(
    expect.objectContaining({ base, expected_generation: 4, proposal_id: "carrier" }),
    "space-a",
  );
  expect(sessionStorage.length).toBe(0);
});

it("isolates recovery and query caches when the data space changes at the same origin", async () => {
  vi.mocked(api.publish).mockRejectedValue(new ApiError("lost response"));
  const view = mount();
  await select();
  fireEvent.click(screen.getByText("Adopt verified candidate into daily"));
  await screen.findByText("lost response");
  const key = sessionStorage.key(0)!;
  const saved = sessionStorage.getItem(key);
  view.switchProject("space-b");
  await screen.findByLabelText("Target parameter branch");
  expect(screen.queryByText("Recover original adoption")).toBeNull();
  expect(screen.getByLabelText("Target parameter branch")).toHaveValue("");
  await waitFor(() => expect(api.branches).toHaveBeenCalledTimes(2));
  expect(sessionStorage.getItem(key)).toBe(saved);
  expect(api.publish).toHaveBeenCalledTimes(1);
});
it("ignores legacy unscoped requests instead of migrating them into the current space", async () => {
  sessionStorage.setItem(
    "candidate-publication:run:carrier",
    JSON.stringify({ name: "daily", run_id: "run", proposal_id: "carrier" }),
  );
  mount();
  await select();
  expect(screen.queryByText("Recover original adoption")).toBeNull();
  expect(api.publish).not.toHaveBeenCalled();
  expect(sessionStorage.length).toBe(1);
});
it.each(["space", "candidate"])(
  "rejects cached requests whose %s binding differs",
  async (binding) => {
    vi.mocked(api.publish).mockRejectedValue(new ApiError("lost response"));
    const view = mount();
    await select();
    fireEvent.click(screen.getByText("Adopt verified candidate into daily"));
    await screen.findByText("lost response");
    const key = sessionStorage.key(0)!;
    const stored = JSON.parse(sessionStorage.getItem(key)!);
    if (binding === "space") stored.projectId = "space-b";
    else stored.candidate.analysisId = "different-analysis";
    sessionStorage.setItem(key, JSON.stringify(stored));
    view.unmount();
    mount();
    fireEvent.click(screen.getByText("Review adoption and records"));
    await screen.findByText(/Stored adoption belongs to a different data space or candidate/);
    expect(screen.queryByText("Recover original adoption")).toBeNull();
    expect(api.publish).toHaveBeenCalledTimes(1);
  },
);
it("rechecks current space before recovery and preserves the request on a server space fence", async () => {
  vi.mocked(api.publish).mockRejectedValueOnce(new ApiError("lost response"));
  mount();
  await select();
  fireEvent.click(screen.getByText("Adopt verified candidate into daily"));
  await screen.findByText("lost response");
  const saved = sessionStorage.getItem(sessionStorage.key(0)!);
  vi.mocked(getHealth).mockResolvedValueOnce({
    status: "ok",
    projectId: "space-b",
    projectName: "B",
    projectRoot: "/b",
    details: {},
  });
  fireEvent.click(screen.getByText("Recover original adoption"));
  await screen.findByText(/Application data space changed. Reopen the candidate/);
  expect(api.publish).toHaveBeenCalledTimes(1);
  expect(sessionStorage.getItem(sessionStorage.key(0)!)).toBe(saved);
  vi.mocked(api.publish).mockRejectedValueOnce(
    new ApiError("Application data space changed; reopen the candidate before adopting", 409),
  );
  fireEvent.click(screen.getByText("Recover original adoption"));
  await screen.findByText("Application data space changed; reopen the candidate before adopting");
  expect(api.publish).toHaveBeenCalledTimes(2);
  expect(api.publish).toHaveBeenLastCalledWith(JSON.parse(saved!).command, "space-a");
  expect(sessionStorage.getItem(sessionStorage.key(0)!)).toBe(saved);
});

vi.mock("../application/OperatorSettings", () => ({
  readOperatorSettings: vi.fn(async () => ({ revision: 1, operator: "local-operator" })),
}));
