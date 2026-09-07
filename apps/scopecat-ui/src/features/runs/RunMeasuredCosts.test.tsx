// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunMeasuredCostsContent } from "./RunMeasuredCosts";

describe("measured run costs", () => {
  it("keeps unavailable counters distinct from zero and warns about overlap and partial history", () => {
    render(
      <RunMeasuredCostsContent
        costs={{
          truncated: true,
          terminal_commit_unavailable_reason: "self commit unavailable",
          finalizations: [],
          operations: [
            {
              operation_id: "trigger",
              instrument_id: "scope",
              operation: "invoke",
              status: "unknown",
              wall_seconds: 0.2,
              wall_source: "server_backend_call",
              connection_generation: "abcdef123456",
              connection_context: "warm",
              measured: {
                source: "adapter-clock",
                unavailable_reason: "unsupported counters",
                uploaded_bytes: 0,
                reused_bytes: 32,
                retained_bytes: 32,
              },
            },
          ],
        }}
      />,
    );
    expect(screen.getByText(/Partial history/)).toBeInTheDocument();
    expect(screen.getByText(/not an additive total/)).toBeInTheDocument();
    expect(screen.getByText("0 B")).toBeInTheDocument();
    expect(screen.getAllByText("32 B")).toHaveLength(2);
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
    expect(screen.getByText(/scope \/ invoke \/ unknown/)).toBeInTheDocument();
  });
});
