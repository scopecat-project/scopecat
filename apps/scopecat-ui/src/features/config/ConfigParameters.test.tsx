// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ConfigProfileSnapshot } from "../../api-contract";
import { ConfigParameters } from "./ConfigParameters";

afterEach(cleanup);

describe("ConfigParameters", () => {
  it("renders typed values and keeps raw JSON behind Advanced", () => {
    const active = snapshot(5, 6.5);
    const selected = snapshot(5.1, 6.6);

    render(<ConfigParameters config={selected} activeConfig={active} />);

    expect(screen.getByText("5 GHz")).toBeInTheDocument();
    expect(screen.getByText("5.1 GHz")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /qubits/i }));
    expect(screen.getByText("q0 (logical_qubit)")).toBeInTheDocument();
    expect(screen.getByText("6.6 GHz")).toBeInTheDocument();
    expect(screen.getByText(/was 6.5 GHz.*Physical value change/)).toBeInTheDocument();

    const advanced = screen.getByText("Advanced · raw snapshot").closest("details");
    expect(advanced).not.toHaveAttribute("open");
    expect(advanced).not.toBeNull();
    expect(within(advanced!).getByText(/profile-5\.1/)).toBeInTheDocument();
  });
});

function snapshot(driveFrequency: number, readoutFrequency: number): ConfigProfileSnapshot {
  return {
    id: `profile-${driveFrequency}`,
    system: {
      id: "system",
      primary_entity_id: "q0",
      topology: {
        entities: [{ id: "q0", kind: "logical_qubit", metadata: {} }],
      },
      instrument_registry: { instruments: [] },
      routing: { roles: [], routes: [] },
      domain_target: null,
      parameter_catalog: {
        id: "calibration",
        definitions: [
          {
            id: "drive.frequency",
            value_type: {
              shape: "scalar",
              atom: { type: "quantity", finite: true, unit: "GHz" },
            },
            description: "Drive frequency",
          },
          {
            id: "qubits",
            value_type: {
              shape: "table",
              columns: [
                {
                  id: "qubit",
                  value_type: {
                    type: "entity",
                    entity_kind: "logical_qubit",
                  },
                },
                {
                  id: "readout_frequency",
                  value_type: { type: "quantity", finite: true, unit: "GHz" },
                },
              ],
              primary_key: ["qubit"],
            },
            description: "Qubit calibration",
          },
        ],
      },
    },
    parameter_snapshot: {
      id: "parameters",
      values: [
        {
          id: "drive.frequency",
          shape: "scalar",
          value: { value: driveFrequency, unit: "GHz" },
        },
        {
          id: "qubits",
          shape: "table",
          rows: [
            {
              qubit: { id: "q0", kind: "logical_qubit", metadata: {} },
              readout_frequency: {
                value: readoutFrequency,
                unit: "GHz",
              },
            },
          ],
        },
      ],
    },
  };
}
