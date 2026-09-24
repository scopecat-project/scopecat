// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  ProcedureRun,
  ProcedureStepAttempt,
  ProcedureStepInputSubmitReceipt,
} from "../../api-contract";
import { DecisionWorkspace } from "./DecisionWorkspace";
import { getProcedureSteps, getWaitingProcedures, submitProcedureInput } from "./decision-api";

vi.mock("./decision-api", () => ({
  getProcedureSteps: vi.fn(),
  getWaitingProcedures: vi.fn(),
  submitProcedureInput: vi.fn(),
}));

const hash = `sha256:${"1".repeat(64)}`;

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  window.history.replaceState(null, "", "/#decisions");
  vi.mocked(getWaitingProcedures).mockResolvedValue({ items: [waitingProcedure()] });
  vi.mocked(getProcedureSteps).mockResolvedValue({
    procedure_run_id: "procedure-1",
    items: [waitingStep()],
  });
  vi.mocked(submitProcedureInput).mockResolvedValue({} as ProcedureStepInputSubmitReceipt);
});

afterEach(() => {
  cleanup();
});

describe("DecisionWorkspace", () => {
  it("shows the retained parameter branch receipt as decision evidence", async () => {
    const step = waitingStep();
    step.inputs = [
      {
        kind: "parameter_publish",
        branch: {
          name: "daily",
          generation: 4,
          actor: "automation",
          note: "",
          revision: { revision_id: "joint-verified", content_hash: hash },
        },
      },
    ];
    vi.mocked(getProcedureSteps).mockResolvedValue({
      procedure_run_id: "procedure-1",
      items: [step],
    });
    renderWorkspace();
    expect(
      await screen.findByText("parameters · daily · generation 4 · joint-verified"),
    ).toBeVisible();
  });

  it("shows exact evidence and submits an identified structured judgment", async () => {
    renderWorkspace();

    expect(await screen.findByText("Select a physical readout resonator")).toBeVisible();
    expect(screen.getByRole("link", { name: "run · readout-s21" })).toHaveAttribute(
      "href",
      "/?run=readout-s21#runs",
    );
    fireEvent.click(screen.getByRole("button", { name: "Edit JSON" }));
    expect(screen.getByLabelText("Structured judgment (JSON)")).toHaveValue(
      JSON.stringify({ resonator: "review this candidate", confidence: 0 }, null, 2),
    );
    fireEvent.change(screen.getByLabelText("Structured judgment (JSON)"), {
      target: { value: '{"resonator":"r2","confidence":0.87}' },
    });
    fireEvent.change(screen.getByLabelText("Recorded reviewer"), {
      target: { value: "analysis-agent" },
    });
    fireEvent.click(screen.getByRole("button", { name: "ai" }));
    fireEvent.change(screen.getByLabelText("Reasoning note (optional)"), {
      target: { value: "isolated dip" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));

    await waitFor(() =>
      expect(submitProcedureInput).toHaveBeenCalledWith(
        {
          procedure_run_id: "procedure-1",
          expected_run_revision: 4,
          step_key: "select-resonator",
          attempt: 1,
          expected_step_revision: 2,
          request_hash: hash,
          actor: "analysis-agent",
          actor_kind: "ai",
          value: { resonator: "r2", confidence: 0.87 },
          note: "isolated dip",
        },
        expect.anything(),
      ),
    );
    expect(screen.getByText(/Decision recorded/)).toBeVisible();
    await waitFor(() => expect(getWaitingProcedures).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getProcedureSteps).toHaveBeenCalledTimes(2));
  });

  it("records a boolean review from the form without editing JSON", async () => {
    const step = waitingStep();
    step.interpretation_request!.structure = {
      type: "object",
      fields: { accept: { type: "bool" }, rationale: { type: "string" } },
    };
    step.interpretation_request!.response_template = {
      accept: false,
      rationale: "Inspect the fits",
    };
    vi.mocked(getProcedureSteps).mockResolvedValue({
      procedure_run_id: "procedure-1",
      items: [step],
    });
    renderWorkspace();
    expect(await screen.findByLabelText("accept")).toHaveValue("false");
    expect(screen.queryByLabelText("Structured judgment (JSON)")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("rationale"), {
      target: { value: "Confirmation differs; reject" },
    });
    fireEvent.change(screen.getByLabelText("Recorded reviewer"), { target: { value: "operator" } });
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    await waitFor(() =>
      expect(submitProcedureInput).toHaveBeenCalledWith(
        expect.objectContaining({
          value: { accept: false, rationale: "Confirmation differs; reject" },
          expected_run_revision: 4,
          expected_step_revision: 2,
          request_hash: hash,
        }),
        expect.anything(),
      ),
    );
  });

  it("retains editable peak frequencies across reopening without JSON or a default zero", async () => {
    const step = waitingStep();
    step.interpretation_request!.structure = {
      type: "object",
      fields: {
        outcome: { type: "literal", values: ["selected", "no_peak", "uncertain"] },
        frequencies: { type: "array", items: { type: "float" } },
      },
    };
    step.interpretation_request!.response_template = { outcome: "uncertain", frequencies: [] };
    step.interpretation_request!.metadata = {
      field_labels: { frequencies: "Peak frequency (MHz)" },
      reviewer: "Alice",
    };
    vi.mocked(getProcedureSteps).mockResolvedValue({
      procedure_run_id: "procedure-1",
      items: [step],
    });
    renderWorkspace();
    await screen.findByLabelText("outcome");
    expect(screen.queryByLabelText("Structured judgment (JSON)")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add Peak frequency (MHz)" }));
    expect(screen.getByLabelText("Peak frequency (MHz) 1")).toHaveValue(null);
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    expect(submitProcedureInput).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Peak frequency (MHz) 1"), {
      target: { value: "6500.25" },
    });
    fireEvent.change(screen.getByLabelText("outcome"), { target: { value: '"selected"' } });
    cleanup();
    renderWorkspace();
    expect(await screen.findByLabelText("Peak frequency (MHz) 1")).toHaveValue(6500.25);
    expect(screen.getByLabelText("Recorded reviewer")).toHaveValue("Alice");
    vi.mocked(getWaitingProcedures).mockResolvedValue({
      items: [{ ...waitingProcedure(), procedure_run_id: "procedure-2" }],
    });
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    await waitFor(() =>
      expect(submitProcedureInput).toHaveBeenCalledWith(
        expect.objectContaining({
          value: { outcome: "selected", frequencies: [6500.25] },
          actor: "Alice",
        }),
        expect.anything(),
      ),
    );
    expect(await screen.findByRole("link", { name: "Open task and continue" })).toHaveAttribute(
      "href",
      "?procedure=procedure-1#launch",
    );
    expect(screen.getByRole("button", { name: "View other waiting decisions" })).toBeVisible();
    expect(localStorage.length).toBe(0);
  });

  it("records an explicit no-peak result without inventing a numeric value", async () => {
    const step = waitingStep();
    step.interpretation_request!.structure = {
      type: "object",
      fields: {
        outcome: { type: "literal", values: ["no_peak", "uncertain"] },
        frequencies: { type: "array", items: { type: "float" } },
      },
    };
    step.interpretation_request!.response_template = { outcome: "no_peak", frequencies: [] };
    step.interpretation_request!.metadata = { reviewer: "Alice" };
    vi.mocked(getProcedureSteps).mockResolvedValue({
      procedure_run_id: "procedure-1",
      items: [step],
    });
    renderWorkspace();
    await screen.findByLabelText("outcome");
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    await waitFor(() =>
      expect(submitProcedureInput).toHaveBeenCalledWith(
        expect.objectContaining({
          value: { outcome: "no_peak", frequencies: [] },
        }),
        expect.anything(),
      ),
    );
  });

  it("explains when no experiment is waiting", async () => {
    vi.mocked(getWaitingProcedures).mockResolvedValue({ items: [] });
    renderWorkspace();

    expect(await screen.findByText("No experiment is waiting for a decision")).toBeVisible();
  });

  it("explains that decisions require the local daemon", () => {
    renderWorkspace({ daemonUnavailable: true });

    expect(screen.getByText("Connect to the local daemon")).toBeVisible();
    expect(getWaitingProcedures).not.toHaveBeenCalled();
  });
});

function renderWorkspace({ daemonUnavailable = false } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <DecisionWorkspace daemonUnavailable={daemonUnavailable} />
    </QueryClientProvider>,
  );
}

function waitingProcedure(): ProcedureRun {
  return {
    procedure_run_id: "procedure-1",
    request_key: "survey-1",
    definition: { id: "lab.readout-survey", version: "1", fingerprint: hash },
    intent: {},
    intent_hash: hash,
    revision: 4,
    samples: [],
    resolved_samples: [],
    state: "waiting_for_input",
  };
}

function waitingStep(): ProcedureStepAttempt {
  return {
    procedure_run_id: "procedure-1",
    step_key: "select-resonator",
    attempt: 1,
    operation: "interpretation",
    intent_hash: hash,
    inputs: [{ kind: "run", run_id: "readout-s21" }],
    revision: 2,
    state: "waiting_for_input",
    updated_at: "2026-09-01T10:00:00+08:00",
    interpretation_request: {
      title: "Select a physical readout resonator",
      instructions: "Exclude cable ripple and duplicated candidates.",
      schema_id: "lab.resonator-selection.v1",
      schema_codec: "scopecat.analysis-fact-schema.v1",
      schema_hash: hash,
      structure: {
        type: "object",
        fields: {
          resonator: { type: "string" },
          confidence: { type: "float" },
        },
      },
      response_template: { resonator: "review this candidate", confidence: 0 },
      metadata: { preferred_view: "readout-s21" },
    },
  };
}
