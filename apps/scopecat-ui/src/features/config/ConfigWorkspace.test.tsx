// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "../../api-client";
import { ConfigWorkspace } from "./ConfigWorkspace";
import { getSetupDefinitions } from "./setup-api";
import {
  commitParameterBranch,
  getParameterRevisions,
  saveParameterRevision,
  type ParameterRevision,
} from "./parameter-api";
vi.mock("./SetupPanel", () => ({ SetupPanel: () => null }));
vi.mock("./setup-api", () => ({ getSetupDefinitions: vi.fn() }));
vi.mock("./parameter-api", () => ({
  getParameterRevisions: vi.fn(),
  saveParameterRevision: vi.fn(),
  commitParameterBranch: vi.fn(),
}));
const base: ParameterRevision = {
  id: "initial",
  content_hash: "sha256:initial",
  actor: "author",
  note: "Measured manually",
  catalog: {
    id: "schema",
    definitions: [
      {
        id: "frequency",
        value_type: { shape: "scalar", atom: { type: "quantity", finite: true, unit: "GHz" } },
      },
      { id: "unknown", value_type: { shape: "scalar", atom: { type: "float", finite: true } } },
    ],
  },
  parameters: {
    id: "values",
    values: [{ id: "frequency", shape: "scalar", value: { value: 4.8, unit: "GHz" } }],
  },
};
let generation = 3;
beforeEach(() => {
  vi.resetAllMocks();
  generation = 3;
  vi.mocked(getSetupDefinitions).mockResolvedValue({ items: [] });
  vi.mocked(getParameterRevisions).mockResolvedValue({ items: [base] });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      const branch = {
        name: "daily",
        generation,
        revision: { revision_id: base.id, content_hash: base.content_hash },
        actor: "author",
        note: "",
      };
      if (path.endsWith("/branches")) return Response.json({ items: [branch], next_cursor: null });
      if (path.endsWith("/branches/daily")) return Response.json(branch);
      if (path.includes("/parameters/revisions/")) return Response.json({ ...base, id: "revised" });
      throw new Error(`Unexpected request: ${path}`);
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function mount() {
  const selected = vi.fn();
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
        })
      }
    >
      <ConfigWorkspace daemonUnavailable={false} onSelectConfiguration={selected} />
    </QueryClientProvider>,
  );
  return selected;
}
async function edit() {
  await screen.findByRole("option", { name: "initial" });
  fireEvent.change(screen.getByLabelText("Saved parameter version"), {
    target: { value: "initial" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Edit a copy" }));
  fireEvent.change(screen.getByLabelText("New version name"), { target: { value: "revised" } });
}
it("selects exact independent parameters without a device or global default", async () => {
  const selected = mount();
  await screen.findByRole("option", { name: "initial" });
  fireEvent.change(screen.getByLabelText("Saved parameter version"), {
    target: { value: "initial" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Use for next experiment" }));
  expect(selected).toHaveBeenCalledWith({
    kind: "parameters",
    ref: { revision_id: "initial", content_hash: "sha256:initial" },
    overrides: [],
  });
  expect(saveParameterRevision).not.toHaveBeenCalled();
  expect(commitParameterBranch).not.toHaveBeenCalled();
});
it("preserves unknown values and retains the draft after validation fails", async () => {
  vi.mocked(saveParameterRevision).mockRejectedValue(new Error("Invalid frequency"));
  mount();
  await edit();
  fireEvent.change(screen.getByLabelText("frequency"), { target: { value: "4.9" } });
  fireEvent.click(screen.getByRole("button", { name: "Save parameter version" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Invalid frequency");
  expect(screen.getByLabelText("frequency")).toHaveValue(4.9);
  expect(saveParameterRevision).toHaveBeenCalledWith(
    expect.objectContaining({
      revision_id: "revised",
      parameters: {
        id: "values",
        values: [{ id: "frequency", shape: "scalar", value: { value: 4.9, unit: "GHz" } }],
      },
    }),
  );
  expect(base.parameters.values).toEqual([
    { id: "frequency", shape: "scalar", value: { value: 4.8, unit: "GHz" } },
  ]);
});
it("keeps the reviewed branch generation until the operator explicitly refreshes it", async () => {
  vi.mocked(commitParameterBranch).mockRejectedValue(
    new ApiError("Branch changed; review latest head", 409),
  );
  mount();
  await edit();
  fireEvent.change(screen.getByLabelText("Named branch (optional)"), {
    target: { value: "daily" },
  });
  await screen.findByText(/Update daily from generation 3/);
  generation = 4;
  const save = screen.getByRole("button", { name: "Save parameter version" });
  fireEvent.click(save);
  await screen.findByRole("alert");
  expect(commitParameterBranch).toHaveBeenLastCalledWith(
    expect.objectContaining({ name: "daily", expected_generation: 3 }),
  );
  fireEvent.click(save);
  await waitFor(() => expect(commitParameterBranch).toHaveBeenCalledTimes(2));
  expect(commitParameterBranch).toHaveBeenLastCalledWith(
    expect.objectContaining({ expected_generation: 3 }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Review latest branch head" }));
  await screen.findByText(/Update daily from generation 4/);
  fireEvent.click(save);
  await waitFor(() =>
    expect(commitParameterBranch).toHaveBeenLastCalledWith(
      expect.objectContaining({ expected_generation: 4 }),
    ),
  );
  expect(screen.getByLabelText("New version name")).toHaveValue("revised");
});
