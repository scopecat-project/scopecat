// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import type { InstrumentProperty } from "../../api-contract";
import { InstrumentPropertyInput, type InstrumentPropertyDraft } from "./InstrumentPropertyInput";
import { engineeringUnit, formatInstrumentQuantity } from "./instrument-quantity";
afterEach(cleanup);
const frequency = {
  id: "frequency",
  label: "频率",
  value_type: { type: "quantity", unit: "Hz", minimum: 1e9, maximum: 6e9 },
} as InstrumentProperty;
function mount(value = 5.1e9, property = frequency) {
  const changed = vi.fn();
  function Editor() {
    const [draft, setDraft] = useState<InstrumentPropertyDraft>();
    return (
      <InstrumentPropertyInput
        property={property}
        currentValue={{
          value,
          unit: property.value_type.type === "quantity" ? property.value_type.unit! : "Hz",
        }}
        draft={draft}
        editable
        ariaLabel="频率"
        onChange={(next) => {
          changed(next);
          setDraft(next);
        }}
      />
    );
  }
  render(<Editor />);
  return changed;
}
describe("device quantity editor", () => {
  it("uses engineering units; changing units is presentation-only", () => {
    const changed = mount();
    expect(screen.getByRole("textbox")).toHaveValue("5.1");
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "MHz" } });
    expect(screen.getByRole("textbox")).toHaveValue("5100");
    expect(changed).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "5200" } });
    expect(changed).toHaveBeenLastCalledWith({
      raw: "5200",
      unit: "MHz",
      value: { value: 5.2e9, unit: "Hz" },
    });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "GHz" } });
    expect(screen.getByRole("textbox")).toHaveValue("5.2");
    expect(changed).toHaveBeenCalledTimes(1);
  });
  it("supports suffix input and validates canonical range, not scaled numbers", () => {
    const changed = mount();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "4900 MHz" } });
    expect(changed.mock.lastCall?.[0].value).toEqual({ value: 4.9e9, unit: "Hz" });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "MHz" } });
    expect(screen.getByRole("textbox")).toHaveValue("4900");
    expect(changed).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "GHz" } });
    expect(screen.getByRole("textbox")).toHaveValue("4900 MHz");
    expect(changed).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "7000 MHz" } });
    expect(changed.mock.lastCall?.[0].value).toBeUndefined();
    expect(screen.getByRole("textbox")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("combobox")).toBeDisabled();
  });
  it("preserves an unchanged value even after a display unit switch", () => {
    const exact = 5100000000.000001;
    const changed = mount(exact);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "MHz" } });
    expect(changed).not.toHaveBeenCalled();
    const input = screen.getByRole("textbox");
    const original = (input as HTMLInputElement).value;
    fireEvent.change(input, { target: { value: "5" } });
    fireEvent.change(input, { target: { value: original } });
    expect(changed.mock.lastCall?.[0].value.value).toBe(exact);
  });
  it.each(["4.1", "4.1 GHz", "4100 MHz", "4.1e9 Hz", "4.2", "4200 MHz"])(
    "accepts exact decimal boundary %s",
    (raw) => {
      const changed = mount(5e9, {
        ...frequency,
        value_type: { type: "quantity", unit: "Hz", minimum: 4.1e9, maximum: 4.2e9 },
      } as InstrumentProperty);
      fireEvent.change(screen.getByRole("textbox"), { target: { value: raw } });
      expect(changed.mock.lastCall?.[0].value).toEqual({
        value: raw.startsWith("4.2") || raw.startsWith("4200") ? 4.2e9 : 4.1e9,
        unit: "Hz",
      });
    },
  );
  it.each(["4.099999999 GHz", "4200.000001 MHz"])(
    "rejects genuinely out of range %s without epsilon",
    (raw) => {
      const changed = mount(5e9, {
        ...frequency,
        value_type: { type: "quantity", unit: "Hz", minimum: 4.1e9, maximum: 4.2e9 },
      } as InstrumentProperty);
      fireEvent.change(screen.getByRole("textbox"), { target: { value: raw } });
      expect(changed.mock.lastCall?.[0].value).toBeUndefined();
    },
  );
  it("retains chosen units across new observations", () => {
    const onChange = vi.fn();
    const props = { property: frequency, editable: true, ariaLabel: "频率", onChange };
    const view = render(<InstrumentPropertyInput {...props} />);
    view.rerender(<InstrumentPropertyInput {...props} currentValue={{ value: 5e9, unit: "Hz" }} />);
    expect(screen.getByRole("combobox")).toHaveValue("GHz");
    view.rerender(<InstrumentPropertyInput {...props} currentValue={{ value: 9e8, unit: "Hz" }} />);
    expect(screen.getByRole("combobox")).toHaveValue("GHz");
    expect(screen.getByRole("textbox")).toHaveValue("0.9");
    expect(onChange).not.toHaveBeenCalled();
  });
  it.each(["", "NaN", "Infinity", "5 V"])("keeps invalid %s input unappliable", (raw) => {
    const changed = mount();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: raw } });
    expect(changed.mock.lastCall?.[0].value).toBeUndefined();
    expect(screen.getByRole("textbox")).toHaveValue(raw);
  });
  it.each(["dBm", "vendor-unit"])("edits %s without unsafe conversions", (unit) => {
    const changed = mount(10, {
      ...frequency,
      value_type: { type: "quantity", unit },
    } as InstrumentProperty);
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "12" } });
    expect(changed.mock.lastCall?.[0].value).toEqual({ value: 12, unit });
  });
  it("does not convert logarithmic or unknown units", () => {
    expect(engineeringUnit(10000, "dBm")).toBe("dBm");
    expect(formatInstrumentQuantity(10000, "vendor-unit")).toBe("10000 vendor-unit");
    expect(formatInstrumentQuantity(5.1e9, "Hz")).toBe("5.1 GHz");
    expect(formatInstrumentQuantity(10000, "Hz")).toBe("10 kHz");
  });
});
