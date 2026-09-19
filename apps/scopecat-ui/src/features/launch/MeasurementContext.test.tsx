// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { LaunchDraft } from "./LaunchDraft";
import { MeasurementContext } from "./MeasurementContext";
vi.mock("../samples/sample-api", () => ({ getSamples: vi.fn(async () => ({ items: [] })) }));
vi.mock("./TargetPicker", () => ({ TargetPicker: () => <p>Exact target chooser</p> }));
vi.mock("../context/ScopeCatalog", () => ({
  ScopeCatalog: ({
    kind,
    value,
    disabled,
    onChange,
  }: {
    kind: string;
    value?: string;
    disabled?: boolean;
    onChange: (value: string) => void;
  }) => (
    <input
      aria-label={kind}
      disabled={disabled}
      value={value ?? ""}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}));
afterEach(cleanup);
function draft(): LaunchDraft {
  return {
    selection: {
      subject: {
        kind: "registered_target",
        ref: {
          catalog_id: "catalog-A",
          target_id: "target-A",
          revision: 3,
          content_hash: "sha256:target",
        },
      },
      configuration: { kind: "active" },
      batch: { kind: "unscoped" },
    },
    actor: "Alice",
    collection: "archive",
    definition: "definition",
    controlDefinition: "control",
    experiment: "rabi",
    controls: {},
    values: {},
    revision: 1,
    pending: false,
    error: "",
    notice: "",
  };
}
function mount(value: LaunchDraft, onChange: (change: Partial<LaunchDraft>) => void) {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MeasurementContext draft={value} projectId="page" onChange={onChange} />
    </QueryClientProvider>,
  );
}
it("allows an explicit batch for a registered target and keeps operator/collection independent", () => {
  const value = draft();
  const onChange = vi.fn();
  mount(value, onChange);
  expect(screen.getByLabelText("Sample ID")).toBeDisabled();
  expect(screen.getByLabelText("batch")).toBeEnabled();
  fireEvent.change(screen.getByLabelText("batch"), { target: { value: "cooldown-B" } });
  expect(onChange).toHaveBeenLastCalledWith({
    selection: { ...value.selection, batch: { kind: "declared", id: "cooldown-B" } },
  });
  fireEvent.change(screen.getByLabelText("Operator"), { target: { value: "Bob" } });
  expect(onChange).toHaveBeenLastCalledWith({ actor: "Bob" });
  fireEvent.change(screen.getByLabelText("collection"), { target: { value: "another-archive" } });
  expect(onChange).toHaveBeenLastCalledWith({ collection: "another-archive" });
});
it("only replaces the exact target after an explicit subject edit, retaining configuration", () => {
  const value = draft();
  value.selection.configuration = {
    kind: "saved",
    ref: { entry_id: "parameters-A", content_hash: "sha256:parameters" },
  };
  const onChange = vi.fn();
  mount(value, onChange);
  fireEvent.click(screen.getByRole("button", { name: "Choose another subject" }));
  expect(onChange).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Sample ID")).toBeEnabled();
  fireEvent.change(screen.getByLabelText("Sample ID"), { target: { value: "chip-B" } });
  expect(onChange).toHaveBeenCalledWith({
    selection: { ...value.selection, subject: { kind: "sample", sample_id: "chip-B" } },
  });
});
