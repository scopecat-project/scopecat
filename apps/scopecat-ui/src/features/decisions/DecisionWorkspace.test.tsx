import type { ProcedureInputContinueReceipt } from "./decision-api";
// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ProcedureRun, ProcedureStepAttempt } from "../../api-contract";
import { DecisionWorkspace } from "./DecisionWorkspace";
import {
  getProcedureSteps,
  getDecisionProgress,
  getWaitingProcedures,
  submitProcedureInput,
  readDecisionDraft,
  saveDecisionDraft,
  getDecisionDraftHistory,
  type DecisionDraftView,
} from "./decision-api";

vi.mock("./DecisionEvidence", () => ({
  DecisionEvidence: ({
    disabled,
    onPick,
  }: {
    disabled: boolean;
    onPick: (value: number) => boolean;
  }) => (
    <button disabled={disabled} onClick={() => onPick(6500)}>
      Pick evidence point
    </button>
  ),
}));

vi.mock("./decision-api", () => ({
  getProcedureSteps: vi.fn(),
  getDecisionProgress: vi.fn(),
  getWaitingProcedures: vi.fn(),
  submitProcedureInput: vi.fn(),
  readDecisionDraft: vi.fn(),
  saveDecisionDraft: vi.fn(),
  getDecisionDraftHistory: vi.fn(),
}));

const hash = `sha256:${"1".repeat(64)}`;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getDecisionProgress).mockResolvedValue({
    dispatch: { management: "active", worker_running: false },
  } as Awaited<ReturnType<typeof getDecisionProgress>>);
  let saved: DecisionDraftView = {
    default_operator: "",
    draft: null,
    head_revision: 0,
    validity: "current",
  };
  vi.mocked(readDecisionDraft).mockImplementation(async () => saved);
  vi.mocked(getDecisionDraftHistory).mockResolvedValue({ items: [] });
  vi.mocked(saveDecisionDraft).mockImplementation(async (command) => {
    const revision = saved.head_revision + 1;
    saved = {
      default_operator: "",
      draft: {
        revision,
        target: command.target,
        baseline: command.baseline,
        input: command.input,
        state: command.discard ? "discarded" : "saved",
        created_at: "2026-10-06T00:00:00Z",
      },
      head_revision: revision,
      validity: "current",
    };
    return saved;
  });
  localStorage.clear();
  window.history.replaceState(null, "", "/#decisions");
  vi.mocked(getWaitingProcedures).mockResolvedValue({ items: [waitingProcedure()] });
  vi.mocked(getProcedureSteps).mockResolvedValue({
    procedure_run_id: "procedure-1",
    items: [waitingStep()],
  });
  vi.mocked(submitProcedureInput).mockResolvedValue({} as ProcedureInputContinueReceipt);
});

afterEach(() => {
  cleanup();
});

describe("DecisionWorkspace", () => {
  it("fills only an active declared field and rejects a stale request without submitting", async () => {
    const step = waitingStep();
    step.inputs = [
      { kind: "analysis", subject: { kind: "run", run_id: "r" }, analysis_record_id: "a" },
    ];
    step.interpretation_request!.structure = {
      type: "object",
      fields: {
        outcome: { type: "literal", values: ["selected", "unsure"] },
        frequency: { type: "union", variants: [{ type: "float" }, { type: "null" }] },
      },
    };
    step.interpretation_request!.response_template = { outcome: "unsure", frequency: null };
    step.interpretation_request!.metadata = {
      point_binding: {
        input: 0,
        output: "trace",
        layer: "data",
        column: "frequency",
        field: "frequency",
        unit: "MHz",
      },
      field_constraints: {
        frequency: {
          required_when: { field: "outcome", value: "selected" },
          empty_otherwise: true,
        },
      },
    };
    vi.mocked(getProcedureSteps).mockResolvedValue({
      procedure_run_id: "procedure-1",
      items: [step],
    });
    const client = renderWorkspace();
    await waitFor(() => expect(readDecisionDraft).toHaveBeenCalled());
    expect(await screen.findByRole("button", { name: "Pick evidence point" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("outcome"), {
      target: { value: JSON.stringify("selected") },
    });
    fireEvent.click(screen.getByRole("button", { name: "Pick evidence point" }));
    expect(screen.getByLabelText("frequency")).toHaveValue("6500");
    expect(submitProcedureInput).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("frequency"), { target: { value: "6500.25" } });
    vi.mocked(getProcedureSteps).mockResolvedValue({
      procedure_run_id: "procedure-1",
      items: [{ ...step, revision: step.revision + 1 }],
    });
    await client.invalidateQueries({ queryKey: ["procedure-decision-steps", "procedure-1"] });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Pick evidence point" })).toBeDisabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Pick evidence point" }));
    expect(screen.getByLabelText("frequency")).toHaveValue("6500.25");
    expect(submitProcedureInput).not.toHaveBeenCalled();
  });

  it("snapshots the default operator before an untouched template's uncertain submission", async () => {
    vi.mocked(readDecisionDraft).mockResolvedValueOnce({
      draft: null,
      head_revision: 0,
      validity: "current",
      default_operator: "Alice",
    });
    vi.mocked(submitProcedureInput).mockRejectedValue(new Error("Connection lost"));
    renderWorkspace();
    expect(await screen.findByLabelText("Recorded reviewer")).toHaveValue("Alice");
    fireEvent.click(screen.getByRole("button", { name: "Submit and continue" }));
    await waitFor(() => expect(submitProcedureInput).toHaveBeenCalledTimes(1));
    const command = vi.mocked(saveDecisionDraft).mock.calls[0]![0];
    expect(command.input.actor).toBe("Alice");
    expect(command.input.actor_kind).toBe("human");
    expect(vi.mocked(saveDecisionDraft).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(submitProcedureInput).mock.invocationCallOrder[0]!,
    );
    cleanup();
    vi.mocked(readDecisionDraft).mockResolvedValue({
      default_operator: "Bob",
      head_revision: 1,
      validity: "current",
      draft: {
        revision: 1,
        target: command.target,
        baseline: command.baseline,
        input: command.input,
        state: "saved",
        created_at: "2026-10-21T00:00:00Z",
      },
    });
    renderWorkspace();
    expect(await screen.findByLabelText("Recorded reviewer")).toHaveValue("Alice");
  });

  it("offers exact continuation retry when queued dispatch fails later", async () => {
    vi.mocked(submitProcedureInput).mockResolvedValue({
      continuation: "queued",
      dispatch_error: null,
    } as ProcedureInputContinueReceipt);
    vi.mocked(getDecisionProgress).mockResolvedValue({
      dispatch: {
        management: "paused",
        worker_running: false,
        input_continuation_retryable: true,
        failure: {
          kind: "dispatch",
          message: "Queued environment unavailable",
          observed_at: "2026-10-19T00:00:00Z",
        },
      },
    } as Awaited<ReturnType<typeof getDecisionProgress>>);
    renderWorkspace();
    await screen.findByLabelText("resonator");
    fireEvent.change(screen.getByLabelText("resonator"), { target: { value: "r2" } });
    fireEvent.change(screen.getByLabelText("confidence"), { target: { value: "0.9" } });
    fireEvent.change(screen.getByLabelText("Recorded reviewer"), { target: { value: "Alice" } });
    vi.mocked(getWaitingProcedures).mockResolvedValue({ items: [] });
    fireEvent.click(screen.getByRole("button", { name: "Submit and continue" }));
    expect(await screen.findByText("Queued environment unavailable")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Retry continuation" }));
    await waitFor(() => expect(submitProcedureInput).toHaveBeenCalledTimes(2));
    expect(vi.mocked(submitProcedureInput).mock.calls[1]![0]).toEqual(
      vi.mocked(submitProcedureInput).mock.calls[0]![0],
    );
  });

  it("retains a saved answer after leaving the waiting list and retries exactly that continuation", async () => {
    vi.mocked(submitProcedureInput)
      .mockResolvedValueOnce({
        continuation: "retryable",
        dispatch_error: "Execution environment unavailable",
      } as ProcedureInputContinueReceipt)
      .mockResolvedValueOnce({
        continuation: "queued",
        dispatch_error: null,
      } as ProcedureInputContinueReceipt);
    renderWorkspace();
    await screen.findByLabelText("resonator");
    fireEvent.change(screen.getByLabelText("resonator"), { target: { value: "r2" } });
    fireEvent.change(screen.getByLabelText("confidence"), { target: { value: "0.9" } });
    fireEvent.change(screen.getByLabelText("Recorded reviewer"), { target: { value: "Alice" } });
    vi.mocked(getWaitingProcedures).mockResolvedValue({ items: [] });
    fireEvent.click(screen.getByRole("button", { name: "Submit and continue" }));
    expect(await screen.findByText("Execution environment unavailable")).toBeVisible();
    const original = vi.mocked(submitProcedureInput).mock.calls[0]![0];
    fireEvent.click(screen.getByRole("button", { name: "Retry continuation" }));
    await waitFor(() => expect(submitProcedureInput).toHaveBeenCalledTimes(2));
    expect(vi.mocked(submitProcedureInput).mock.calls[1]![0]).toEqual(original);
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Retry continuation" })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("link", { name: "Open task" })).toHaveAttribute(
      "href",
      "?procedure=procedure-1#launch",
    );
  });

  it("shows a saved but blocked continuation without offering an unlock retry", async () => {
    vi.mocked(submitProcedureInput).mockResolvedValue({
      continuation: "blocked",
      dispatch_error: "Task was paused; review the task",
    } as ProcedureInputContinueReceipt);
    renderWorkspace();
    await screen.findByLabelText("resonator");
    fireEvent.change(screen.getByLabelText("resonator"), { target: { value: "r2" } });
    fireEvent.change(screen.getByLabelText("confidence"), { target: { value: "0.9" } });
    fireEvent.change(screen.getByLabelText("Recorded reviewer"), { target: { value: "Alice" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit and continue" }));
    expect(await screen.findByText("Task was paused; review the task")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Retry continuation" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /save only/i })).not.toBeInTheDocument();
  });

  it("keeps numeric conditional input visible through editing and submits normalized values", async () => {
    const step = waitingStep();
    step.interpretation_request!.structure = {
      type: "object",
      fields: {
        mode: { type: "int" },
        detail: { type: "union", variants: [{ type: "float" }, { type: "null" }] },
      },
    };
    step.interpretation_request!.response_template = { mode: 1, detail: 2 };
    step.interpretation_request!.metadata = {
      reviewer: "Reviewer",
      field_constraints: {
        detail: { required_when: { field: "mode", value: 1 }, empty_otherwise: true },
      },
    };
    vi.mocked(getProcedureSteps).mockResolvedValue({
      procedure_run_id: "procedure-1",
      items: [step],
    });
    renderWorkspace();
    const mode = await screen.findByLabelText("mode");
    for (const text of ["1", "", "-", "1e", "1.5"]) {
      fireEvent.change(mode, { target: { value: text } });
      expect(screen.getByLabelText("detail")).toHaveValue("2");
    }
    fireEvent.change(mode, { target: { value: "0" } });
    expect(screen.queryByLabelText("detail")).not.toBeInTheDocument();
    fireEvent.change(mode, { target: { value: "1" } });
    expect(screen.getByLabelText("detail")).toHaveValue("2");
    fireEvent.click(screen.getByRole("button", { name: "Submit and continue" }));
    await waitFor(() =>
      expect(submitProcedureInput).toHaveBeenCalledWith(
        expect.objectContaining({ value: { mode: 1, detail: 2 } }),
        expect.anything(),
      ),
    );
  });
  it("distinguishes same-name waiting requests with visible context and selects the exact task", async () => {
    const first = {
      ...waitingProcedure(),
      procedure_run_id: "task-aaaaaaaa",
      created_at: "2026-10-19T08:00:00Z",
    };
    const second = { ...first, procedure_run_id: "task-bbbbbbbb" };
    vi.mocked(getWaitingProcedures).mockResolvedValue({ items: [first, second] });
    renderWorkspace();
    const choice = await screen.findByRole("button", {
      name: /lab.readout-survey.*Submitted.*bbbbbbbb/,
    });
    expect(screen.getByRole("button", { name: /Reference.*aaaaaaaa/ })).toBeVisible();
    fireEvent.click(choice);
    await waitFor(() =>
      expect(getProcedureSteps).toHaveBeenCalledWith(
        second.procedure_run_id,
        expect.any(AbortSignal),
      ),
    );
    expect(await screen.findByPlaceholderText("Person’s name")).toBeVisible();
  });
  it("keeps decisions without analysis evidence full width", async () => {
    renderWorkspace();
    const field = await screen.findByLabelText("resonator");
    const layout = field.closest("fieldset")!.parentElement!;
    expect(layout).not.toHaveClass("xl:grid-cols-2");
    expect(layout.children).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Open measurement data" })).toBeInTheDocument();
  });

  it("renders the shipped nullable peak schema and blocks invalid combinations before recording", async () => {
    const step = waitingStep();
    step.interpretation_request!.structure = {
      type: "object",
      fields: {
        outcome: { type: "literal", values: ["Peak selected", "No clear peak", "Unsure"] },
        frequency_mhz: { type: "union", variants: [{ type: "float" }, { type: "null" }] },
      },
    };
    step.interpretation_request!.response_template = { outcome: "Unsure", frequency_mhz: null };
    step.interpretation_request!.metadata = {
      reviewer: "learner",
      field_labels: { frequency_mhz: "Peak frequency (MHz)" },
      field_constraints: {
        frequency_mhz: {
          minimum: 6490,
          maximum: 6510,
          required_when: { field: "outcome", value: "Peak selected" },
          empty_otherwise: true,
        },
      },
    };
    vi.mocked(getProcedureSteps).mockResolvedValue({
      procedure_run_id: "procedure-1",
      items: [step],
    });
    renderWorkspace();
    await screen.findByLabelText("outcome");
    expect(screen.queryByLabelText("Peak frequency (MHz)")).not.toBeInTheDocument();
    expect(screen.queryByText("Structured judgment (JSON)")).toBeNull();
    fireEvent.change(screen.getByLabelText("outcome"), {
      target: { value: JSON.stringify("Peak selected") },
    });
    const frequency = screen.getByLabelText("Peak frequency (MHz)");
    fireEvent.click(screen.getByRole("button", { name: "Submit and continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter Peak frequency");
    fireEvent.change(frequency, { target: { value: "6600" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit and continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("between 6490 and 6510");
    expect(submitProcedureInput).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("outcome"), {
      target: { value: JSON.stringify("No clear peak") },
    });
    expect(screen.queryByLabelText("Peak frequency (MHz)")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("outcome"), {
      target: { value: JSON.stringify("Peak selected") },
    });
    expect(screen.getByLabelText("Peak frequency (MHz)")).toHaveValue("6600");
    fireEvent.change(screen.getByLabelText("outcome"), {
      target: { value: JSON.stringify("No clear peak") },
    });
    fireEvent.click(screen.getByRole("button", { name: "Submit and continue" }));
    await waitFor(() =>
      expect(submitProcedureInput).toHaveBeenCalledWith(
        expect.objectContaining({
          value: { outcome: "No clear peak", frequency_mhz: null },
        }),
        expect.anything(),
      ),
    );
  });

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
    await screen.findByText("Parameters · daily");
    expect(screen.getByText("Parameters · daily")).toBeVisible();
  });

  it("shows exact evidence and submits an identified structured judgment", async () => {
    renderWorkspace();

    expect(await screen.findByText("Select a physical readout resonator")).toBeVisible();
    expect(screen.getByRole("link", { name: "Open measurement data" })).toHaveAttribute(
      "href",
      "/?run=readout-s21#runs",
    );
    expect(screen.queryByText("Expected structure")).not.toBeInTheDocument();
    expect(screen.queryByText("Edit JSON")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("resonator"), { target: { value: "r2" } });
    fireEvent.change(screen.getByLabelText("confidence"), { target: { value: "0.87" } });
    fireEvent.change(screen.getByLabelText("Recorded reviewer"), {
      target: { value: "analysis-agent" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Decision options" }));
    fireEvent.change(screen.getByLabelText("Reasoning note (optional)"), {
      target: { value: "isolated dip" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Submit and continue" }));

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
          actor_kind: "human",
          value: { resonator: "r2", confidence: 0.87 },
          note: "isolated dip",
        },
        expect.anything(),
      ),
    );
    expect(screen.getByText(/Decision saved/)).toBeVisible();
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
    fireEvent.click(screen.getByRole("button", { name: "Submit and continue" }));
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
    expect(screen.getByLabelText("Peak frequency (MHz) 1")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "Submit and continue" }));
    expect(submitProcedureInput).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Peak frequency (MHz) 1"), {
      target: { value: "6500.25" },
    });
    fireEvent.change(screen.getByLabelText("outcome"), { target: { value: '"selected"' } });
    cleanup();
    renderWorkspace();
    expect(await screen.findByLabelText("Peak frequency (MHz) 1")).toHaveValue("6500.25");
    expect(screen.getByLabelText("Recorded reviewer")).toHaveValue("Alice");
    vi.mocked(getWaitingProcedures).mockResolvedValue({
      items: [{ ...waitingProcedure(), procedure_run_id: "procedure-2" }],
    });
    fireEvent.click(screen.getByRole("button", { name: "Submit and continue" }));
    await waitFor(() =>
      expect(submitProcedureInput).toHaveBeenCalledWith(
        expect.objectContaining({
          value: { outcome: "selected", frequencies: [6500.25] },
          actor: "Alice",
        }),
        expect.anything(),
      ),
    );
    expect(await screen.findByRole("link", { name: "Open task" })).toHaveAttribute(
      "href",
      "?procedure=procedure-1#launch",
    );
    expect(screen.getByRole("button", { name: "View waiting decisions" })).toBeVisible();
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
    fireEvent.click(screen.getByRole("button", { name: "Submit and continue" }));
    await waitFor(() =>
      expect(submitProcedureInput).toHaveBeenCalledWith(
        expect.objectContaining({
          value: { outcome: "no_peak", frequencies: [] },
        }),
        expect.anything(),
      ),
    );
  });

  it("blocks unsupported response types without offering raw JSON or dropping fields", async () => {
    const step = waitingStep();
    step.interpretation_request!.structure = {
      type: "object",
      fields: {
        accepted: { type: "bool" },
        frequency: { type: "quantity" },
      },
    };
    step.interpretation_request!.response_template = {
      accepted: true,
      frequency: { value: 6, unit: "GHz" },
    };
    vi.mocked(getProcedureSteps).mockResolvedValue({
      procedure_run_id: "procedure-1",
      items: [step],
    });
    renderWorkspace();
    expect(await screen.findByRole("alert")).toHaveTextContent("form does not support yet");
    expect(screen.getByRole("button", { name: "Submit and continue" })).toBeDisabled();
    expect(screen.queryByText("Expected structure")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Decision options" }));
    expect(screen.queryByText("Edit JSON")).not.toBeInTheDocument();
    fireEvent.submit(screen.getByRole("form", { name: "Record judgment" }));
    expect(submitProcedureInput).not.toHaveBeenCalled();
  });

  it("guards composition and duplicate form submission while saving the draft", async () => {
    const save = vi.mocked(saveDecisionDraft).getMockImplementation()!;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.mocked(saveDecisionDraft).mockImplementation(async (command) => {
      await gate;
      return save(command);
    });
    renderWorkspace();
    const reviewer = await screen.findByLabelText("Recorded reviewer");
    fireEvent.change(reviewer, { target: { value: "Alice" } });
    const form = screen.getByRole("form", { name: "Record judgment" });
    fireEvent.compositionStart(reviewer);
    const enter = new KeyboardEvent("keydown", {
      key: "Enter",
      bubbles: true,
      cancelable: true,
      isComposing: true,
    });
    reviewer.dispatchEvent(enter);
    expect(enter.defaultPrevented).toBe(true);
    fireEvent.submit(form);
    expect(submitProcedureInput).not.toHaveBeenCalled();
    fireEvent.compositionEnd(reviewer);
    fireEvent.submit(form);
    fireEvent.submit(form);
    await waitFor(() => expect(saveDecisionDraft).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "Submit and continue" })).toBeDisabled();
    expect(submitProcedureInput).not.toHaveBeenCalled();
    release();
    await waitFor(() => expect(submitProcedureInput).toHaveBeenCalledTimes(1));
    expect(vi.mocked(submitProcedureInput).mock.calls[0]![0]).toMatchObject({
      expected_run_revision: 4,
      expected_step_revision: 2,
      request_hash: hash,
      actor: "Alice",
      value: { resonator: "review this candidate", confidence: 0 },
    });
  });

  it("keeps the submitted input editable after a server conflict", async () => {
    vi.mocked(submitProcedureInput).mockRejectedValue(
      new Error("Request revision changed; review the current request."),
    );
    renderWorkspace();
    const reviewer = await screen.findByLabelText("Recorded reviewer");
    fireEvent.change(reviewer, { target: { value: "Alice" } });
    fireEvent.submit(screen.getByRole("form", { name: "Record judgment" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Request revision changed");
    expect(reviewer).toHaveValue("Alice");
    expect(reviewer).toBeEnabled();
    expect(screen.getByLabelText("confidence")).toHaveValue("0");
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
  return client;
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
