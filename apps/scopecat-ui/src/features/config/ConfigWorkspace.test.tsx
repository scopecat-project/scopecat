// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ConfigWorkspace } from "./ConfigWorkspace";
import { getSetupDefinitions } from "./setup-api";
import {
  commitParameterBranch,
  getParameterRevisions,
  saveParameterRevision,
  type ParameterRevision,
} from "./parameter-api";
import {
  startParameterDraft,
  saveParameterDraft,
  commitParameterDraft,
  freezeParameterDraft,
  parameterDraftHistory,
  type ParameterDraftView,
} from "./parameter-draft-api";
vi.mock("./parameter-draft-api", () => ({
  startParameterDraft: vi.fn(),
  saveParameterDraft: vi.fn(),
  commitParameterDraft: vi.fn(),
  freezeParameterDraft: vi.fn(),
  parameterDraftHistory: vi.fn(),
  readParameterDraft: vi.fn(),
}));
let draftView: ParameterDraftView;
vi.mock("./ConfigurationExchange", () => ({
  ConfigurationExchange: ({ onCreated }: { onCreated: (revision: string) => Promise<void> }) => (
    <button onClick={() => void onCreated("imported")}>Create imported test copy</button>
  ),
}));
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
  draftView = {
    head_revision: 1,
    branch_changed: false,
    draft: {
      draft_id: "11111111-1111-4111-8111-111111111111",
      revision: 1,
      working_branch: "",
      base: { revision_id: base.id, content_hash: base.content_hash },
      state: "saved",
      created_at: "2026-10-06T10:00:00Z",
      input: {
        name: "initial (revised)",
        actor: "author",
        branch: "",
        note: "",
        values: [{ id: "frequency", shape: "scalar", value: { text: "4.8", unit: "GHz" } }],
      },
    },
  };
  vi.mocked(startParameterDraft).mockImplementation(async () => structuredClone(draftView));
  vi.mocked(parameterDraftHistory).mockResolvedValue({ items: [] });
  vi.mocked(saveParameterDraft).mockImplementation(async (_id, command) => {
    draftView = {
      ...draftView,
      head_revision: draftView.head_revision + 1,
      draft: {
        ...draftView.draft,
        input: command.input,
        revision: draftView.head_revision + 1,
        state: command.discard ? "discarded" : "saved",
      },
    };
    return structuredClone(draftView);
  });
  vi.mocked(commitParameterDraft).mockRejectedValue(new Error("Invalid frequency"));
  vi.mocked(freezeParameterDraft).mockImplementation(async () => ({
    draft_id: draftView.draft.draft_id,
    revision: draftView.head_revision,
    configuration: { kind: "parameters", ref: draftView.draft.base, overrides: [] },
  }));
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
      if (path.includes("/branches/")) return Response.json(branch);
      if (path.includes("/parameters/revisions/")) return Response.json({ ...base, id: "revised" });
      throw new Error(`Unexpected request: ${path}`);
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function Availability() {
  const [offline, setOffline] = useState(false);
  return (
    <>
      <button onClick={() => setOffline(true)}>Disconnect service</button>
      <ConfigWorkspace daemonUnavailable={offline} />
    </>
  );
}
function mount(availability = false) {
  const selected = vi.fn();
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
        })
      }
    >
      {availability ? (
        <Availability />
      ) : (
        <ConfigWorkspace daemonUnavailable={false} onSelectConfiguration={selected} />
      )}
    </QueryClientProvider>,
  );
  return selected;
}
async function edit() {
  await screen.findByRole("option", { name: "initial" });
  fireEvent.change(screen.getByLabelText("Saved parameter version"), {
    target: { value: "initial" },
  });
  fireEvent.click(await screen.findByRole("button", { name: "Edit a copy" }));
  await screen.findByLabelText("New version name");
  fireEvent.change(screen.getByLabelText("New version name"), { target: { value: "revised" } });
}
it("selects exact independent parameters without a device or global default", async () => {
  const selected = mount();
  await screen.findByRole("option", { name: "initial" });
  fireEvent.change(screen.getByLabelText("Saved parameter version"), {
    target: { value: "initial" },
  });
  fireEvent.click(await screen.findByRole("button", { name: "Use for next experiment" }));
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
  expect(screen.getByLabelText("frequency")).toHaveValue("4.9");
  expect(draftView.draft.input.name).toBe("revised");
  expect(draftView.draft.input.values).toEqual([
    { id: "frequency", shape: "scalar", value: { text: "4.9", unit: "GHz" } },
  ]);
  expect(commitParameterDraft).toHaveBeenCalled();
  expect(base.parameters.values).toEqual([
    { id: "frequency", shape: "scalar", value: { value: 4.8, unit: "GHz" } },
  ]);
});
it("captures working inputs without saving a parameter version", async () => {
  const selected = mount();
  await edit();
  fireEvent.change(screen.getByLabelText("frequency"), { target: { value: "1e" } });
  fireEvent.click(screen.getByRole("button", { name: "Use working inputs for next experiment" }));
  await waitFor(() => expect(selected).toHaveBeenCalled());
  expect(draftView.draft.input.values?.[0]?.value?.text).toBe("1e");
  expect(freezeParameterDraft).toHaveBeenCalledWith(
    draftView.draft.draft_id,
    draftView.head_revision,
  );
  expect(commitParameterDraft).not.toHaveBeenCalled();
  expect(saveParameterRevision).not.toHaveBeenCalled();
});

it("keeps an unsaved parameter draft when an import creates another copy", async () => {
  mount();
  await edit();
  fireEvent.change(screen.getByLabelText("frequency"), { target: { value: "4.9" } });
  fireEvent.click(screen.getByRole("button", { name: "Create imported test copy" }));
  await waitFor(() => expect(getParameterRevisions).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText("frequency")).toHaveValue("4.9");
  expect(screen.getByLabelText("New version name")).toHaveValue("revised");
});

it("does not reuse a reviewed branch head after changing its destination", async () => {
  mount();
  await edit();
  fireEvent.change(screen.getByLabelText("Named branch (optional)"), {
    target: { value: "daily" },
  });
  await screen.findByText(/Latest read generation: 3/);
  fireEvent.click(screen.getByRole("button", { name: "Review latest branch head" }));
  await screen.findByRole("button", { name: "Keep my table after branch review" });
  fireEvent.change(screen.getByLabelText("Named branch (optional)"), {
    target: { value: "other" },
  });
  expect(
    screen.queryByRole("button", { name: "Keep my table after branch review" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save parameter version" })).toBeDisabled();
});

it("keeps failed input visible when closing or switching the saved version", async () => {
  mount();
  await edit();
  vi.mocked(saveParameterDraft).mockRejectedValue(new Error("offline"));
  fireEvent.change(screen.getByLabelText("frequency"), { target: { value: "1e" } });
  fireEvent.click(screen.getByRole("button", { name: "Close editor" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("offline");
  expect(screen.getByLabelText("frequency")).toHaveValue("1e");
  fireEvent.change(screen.getByLabelText("Saved parameter version"), { target: { value: "" } });
  await waitFor(() => expect(saveParameterDraft).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText("Saved parameter version")).toHaveValue("initial");
  expect(screen.getByLabelText("frequency")).toHaveValue("1e");
  vi.mocked(saveParameterDraft).mockImplementation(async (_id, command) => ({
    ...draftView,
    head_revision: 2,
    draft: { ...draftView.draft, revision: 2, input: command.input },
  }));
  fireEvent.click(screen.getByRole("button", { name: "Close editor" }));
  await waitFor(() => expect(screen.queryByLabelText("frequency")).not.toBeInTheDocument());
  expect(vi.mocked(saveParameterDraft).mock.calls.at(-1)?.[1].input.values?.[0]?.value?.text).toBe(
    "1e",
  );
});

it("keeps the editor and failed input mounted while the service is unavailable", async () => {
  mount(true);
  await edit();
  vi.mocked(saveParameterDraft).mockRejectedValue(new Error("offline"));
  fireEvent.change(screen.getByLabelText("frequency"), { target: { value: "-" } });
  fireEvent.click(screen.getByRole("button", { name: "Disconnect service" }));
  expect(screen.getByLabelText("frequency")).toHaveValue("-");
  expect(await screen.findByText("offline")).toBeVisible();
  expect(screen.getByRole("button", { name: "Retry draft save" })).toBeVisible();
});

it("ignores a completed editor's delayed read after another draft is opened", async () => {
  mount();
  await edit();
  const request = vi.mocked(fetch).getMockImplementation()!;
  let release!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation((...args) => {
    if (new URL((args[0] as Request).url).pathname.endsWith("/revisions/revised"))
      return new Promise((resolve) => {
        release = resolve;
      });
    return request(...args);
  });
  vi.mocked(commitParameterDraft).mockImplementation(async () => ({
    ...draftView,
    draft: {
      ...draftView.draft,
      state: "completed",
      result: { revision_id: "revised", content_hash: "sha256:revised" },
    },
  }));
  fireEvent.click(screen.getByRole("button", { name: "Save parameter version" }));
  await screen.findByText(/This draft is completed/);
  fireEvent.click(
    screen
      .getAllByRole("button", { name: "Close editor" })
      .find((button) => !button.closest("fieldset[disabled]"))!,
  );
  await waitFor(() => expect(screen.queryByLabelText("frequency")).not.toBeInTheDocument());
  draftView = {
    ...draftView,
    draft: { ...draftView.draft, draft_id: "22222222-2222-4222-8222-222222222222" },
  };
  fireEvent.click(screen.getByRole("button", { name: "Edit a copy" }));
  await screen.findByLabelText("frequency");
  vi.mocked(saveParameterDraft).mockRejectedValue(new Error("offline"));
  fireEvent.change(screen.getByLabelText("frequency"), { target: { value: "1e" } });
  await act(async () => release(Response.json({ ...base, id: "revised" })));
  await waitFor(() => expect(getParameterRevisions).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText("frequency")).toHaveValue("1e");
  expect(screen.getByLabelText("Saved parameter version")).toHaveValue("initial");
});
