// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { LaunchWorkspace } from "./LaunchWorkspace";

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

it("shows a durable resource wait and its cancelled child without calling it running", async () => {
  window.history.replaceState(null, "", "/?procedure=p1#launch");
  let cancelled = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/experiment-launcher")) return Response.json({ entries: [] });
      if (path.endsWith("/steps"))
        return Response.json({
          items: [{ step_key: "child", attempt: 1, state: "running" }],
          next_cursor: null,
        });
      if (path.endsWith("/cancel")) {
        cancelled = true;
        return Response.json({});
      }
      return Response.json({
        procedure_run_id: "p1",
        revision: 7,
        state: cancelled ? "closed" : "ready",
        resource_wait: { step_key: "child", run_id: "run-child" },
        closure: cancelled ? { status: "cancelled", actor: "operator" } : null,
      });
    }),
  );
  mount();
  await screen.findByText("Waiting for resources");
  expect(screen.getByRole("link", { name: /Inspect waiting child/ })).toHaveAttribute(
    "href",
    "?run=run-child#runs",
  );
  fireEvent.click(screen.getByText("Cancel remaining procedure"));
  fireEvent.change(screen.getByLabelText("Cancellation actor"), { target: { value: "operator" } });
  fireEvent.change(screen.getByLabelText("Cancellation reason"), {
    target: { value: "Stop waiting" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Cancel procedure" }));
  await screen.findByText("child: Cancelled before acquisition");
});
const entry = {
  id: "rabi",
  version: "1",
  actions: ["preview"],
  kind: "calibration",
  configuration_effect: "candidate",
  title: "Rabi",
  description: "Configured pulse",
  request: {
    required: ["qubit", "amplitude_max"],
    properties: {
      qubit: { type: "string", title: "Qubit", enum: ["Q12"] },
      amplitude_max: { type: "number", title: "Amplitude", maximum: 0.9 },
    },
  },
};
const previewResult = {
  experiment_id: "rabi",
  request_hash: "sha256:" + "a".repeat(64),
  point_count: 2,
  config_source: {
    kind: "config_registry",
    selector: "active",
    entry_id: "baseline",
    config_ref: "baseline",
    content_hash: "sha256:" + "b".repeat(64),
    registry_generation: 1,
  },
  summary: "Configured pulse",
  resolved_inputs: {},
};
function mount() {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <LaunchWorkspace />
    </QueryClientProvider>,
  );
}
it("previews a typed request and clears results after edits", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ entries: [entry] }))
    .mockResolvedValueOnce(Response.json(previewResult));
  vi.stubGlobal("fetch", fetcher);
  mount();
  fireEvent.change(await screen.findByLabelText("Qubit"), { target: { value: "Q12" } });
  fireEvent.change(screen.getByLabelText("Amplitude"), { target: { value: "0.4" } });
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  await screen.findByText("Preview ready");
  const request = fetcher.mock.calls[1]?.[0] as Request;
  expect(await request.json()).toEqual({
    action: "preview",
    actor: "operator",
    request_key: "",
    experiment: "rabi",
    version: "1",
    sample: null,
    inputs: { qubit: "Q12", amplitude_max: 0.4 },
  });
  fireEvent.change(screen.getByLabelText("Amplitude"), { target: { value: "0.3" } });
  await waitFor(() => expect(screen.queryByText("Preview ready")).toBeNull());
});
it("shows a project without registered experiments", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ entries: [] })));
  mount();
  expect(
    await screen.findByText("This project has no registered experiments."),
  ).toBeInTheDocument();
});
it("shows compilation failure without a successful preview", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(Response.json({ entries: [entry] }))
      .mockResolvedValueOnce(Response.json({ detail: "binding unavailable" }, { status: 422 })),
  );
  mount();
  fireEvent.change(await screen.findByLabelText("Qubit"), { target: { value: "Q12" } });
  fireEvent.change(screen.getByLabelText("Amplitude"), { target: { value: "0.4" } });
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("binding unavailable");
  expect(screen.queryByText("Preview ready")).toBeNull();
});

it("retains the submission key after a lost response and opens durable progress", async () => {
  window.history.replaceState(null, "", "/#launch");
  const submitted: Array<{ request_key: string }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/experiment-launcher"))
        return Response.json({ entries: [{ ...entry, actions: ["preview", "submit"] }] });
      if (path.endsWith("/preview")) return Response.json(previewResult);
      if (path.endsWith("/submit")) {
        submitted.push(await request.json());
        if (submitted.length === 1) throw new TypeError("connection lost");
        return Response.json({ procedure_id: "p1", dispatch_error: null });
      }
      if (path.endsWith("/steps")) return Response.json({ items: [], next_cursor: null });
      return Response.json({ procedure_run_id: "p1", state: "waiting_for_input", closure: null });
    }),
  );
  mount();
  fireEvent.change(await screen.findByLabelText("Qubit"), { target: { value: "Q12" } });
  fireEvent.change(screen.getByLabelText("Amplitude"), { target: { value: "0.4" } });
  fireEvent.change(screen.getByLabelText("Sample ID"), { target: { value: "chip" } });
  fireEvent.change(screen.getByLabelText("Operator"), { target: { value: "reviewer" } });
  expect(screen.getByRole("button", { name: "Start acquisition" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  await screen.findByText("Preview ready");
  fireEvent.click(screen.getByRole("button", { name: "Start acquisition" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Start acquisition" }));
  await screen.findByText("Procedure progress");
  expect(submitted).toHaveLength(2);
  expect(submitted[0]?.request_key).toBe(submitted[1]?.request_key);
  expect(new URLSearchParams(window.location.search).get("procedure")).toBe("p1");
});

it("submits selected array members and invalidates the preview when membership changes", async () => {
  window.history.replaceState(null, "", "/#launch");
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({
        entries: [
          {
            ...entry,
            id: "allxy",
            request: {
              required: ["qubits"],
              properties: {
                qubits: {
                  type: "array",
                  title: "Qubits",
                  items: { type: "string", enum: ["Q04", "Q12", "Q24"] },
                },
              },
            },
          },
        ],
      }),
    )
    .mockResolvedValueOnce(Response.json(previewResult));
  vi.stubGlobal("fetch", fetcher);
  mount();
  const select = (await screen.findByLabelText("Qubits")) as HTMLSelectElement;
  select.options[0]!.selected = true;
  select.options[2]!.selected = true;
  fireEvent.change(select);
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  await screen.findByText("Preview ready");
  expect((await (fetcher.mock.calls[1]![0] as Request).json()).inputs).toEqual({
    qubits: ["Q04", "Q24"],
  });
  select.options[2]!.selected = false;
  fireEvent.change(select);
  expect(screen.queryByText("Preview ready")).toBeNull();
});

it("cancels a waiting procedure with the observed revision and recorded actor", async () => {
  window.history.replaceState(null, "", "/?procedure=p1#launch");
  let cancelled = false;
  let command: unknown;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/experiment-launcher")) return Response.json({ entries: [entry] });
      if (path.endsWith("/steps"))
        return Response.json({
          items: [{ step_key: "review", attempt: 1, state: "waiting_for_input" }],
          next_cursor: null,
        });
      if (path.endsWith("/cancel")) {
        command = await request.json();
        cancelled = true;
        return Response.json({});
      }
      return Response.json({
        procedure_run_id: "p1",
        revision: 7,
        state: cancelled ? "closed" : "waiting_for_input",
        closure: cancelled ? { status: "cancelled", actor: "reviewer", reason: "Stop here" } : null,
      });
    }),
  );
  mount();
  fireEvent.click(await screen.findByText("Cancel remaining procedure"));
  expect(screen.getByRole("button", { name: "Cancel procedure" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Cancellation actor"), { target: { value: "reviewer" } });
  fireEvent.change(screen.getByLabelText("Cancellation reason"), {
    target: { value: "Stop here" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Cancel procedure" }));
  await screen.findByText("Closed by reviewer");
  expect(screen.getByText("review: Review cancelled")).toBeInTheDocument();
  expect(command).toEqual({
    procedure_run_id: "p1",
    expected_run_revision: 7,
    actor: "reviewer",
    reason: "Stop here",
  });
  expect(screen.queryByRole("button", { name: "Resume execution" })).toBeNull();
});

it("keeps a running cancellation pending instead of reporting a stopped procedure", async () => {
  window.history.replaceState(null, "", "/?procedure=p1#launch");
  let pending = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/experiment-launcher")) return Response.json({ entries: [entry] });
      if (path.endsWith("/steps")) return Response.json({ items: [], next_cursor: null });
      if (path.endsWith("/cancel")) {
        pending = true;
        return Response.json({});
      }
      return Response.json({
        procedure_run_id: "p1",
        revision: 7,
        state: "leased",
        closure: null,
        cancellation: pending ? { actor: "operator", reason: "Enough" } : null,
      });
    }),
  );
  mount();
  fireEvent.click(await screen.findByText("Cancel remaining procedure"));
  fireEvent.change(screen.getByLabelText("Cancellation actor"), { target: { value: "operator" } });
  fireEvent.change(screen.getByLabelText("Cancellation reason"), { target: { value: "Enough" } });
  fireEvent.click(screen.getByRole("button", { name: "Stop after current step" }));
  await screen.findByText("Cancellation requested — finishing current step");
  expect(screen.queryByText("Cancelled")).toBeNull();
  expect(screen.queryByRole("button", { name: "Stop after current step" })).toBeNull();
});

it.each(["Operator", "Sample ID"])("invalidates preview after changing %s", async (label) => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({ entries: [{ ...entry, actions: ["preview", "submit"] }] }),
      )
      .mockResolvedValueOnce(Response.json(previewResult)),
  );
  mount();
  fireEvent.change(await screen.findByLabelText("Qubit"), { target: { value: "Q12" } });
  fireEvent.change(screen.getByLabelText("Amplitude"), { target: { value: "0.4" } });
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  await screen.findByText("Preview ready");
  expect(screen.getByRole("button", { name: "Start acquisition" })).toBeEnabled();
  fireEvent.change(screen.getByLabelText(label), { target: { value: "changed" } });
  expect(screen.queryByText("Preview ready")).toBeNull();
  expect(screen.getByRole("button", { name: "Start acquisition" })).toBeDisabled();
});

it.each([{ type: "integer", enum: [1, 2] }, { type: ["number", "null"] }, false])(
  "reports unsupported project controls explicitly",
  async (field) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json({
          entries: [
            {
              ...entry,
              request: { properties: { custom: field } },
            },
          ],
        }),
      ),
    );
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("project-specific form");
    expect(screen.getByRole("button", { name: "Preview" })).toBeDisabled();
  },
);

it("does not offer actions absent from catalog capabilities", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ entries: [{ ...entry, actions: [] }] })),
  );
  mount();
  await screen.findByLabelText("Qubit");
  expect(screen.queryByRole("button", { name: "Preview" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Start acquisition" })).toBeNull();
});
