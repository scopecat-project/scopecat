// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { components } from "../../api-schema";
import { RunFailureEvidenceContent } from "./RunFailureEvidence";

afterEach(cleanup);

function evidence(): components["schemas"]["RunFailureEvidence"] {
  return {
    primary: {
      code: "detector_acquisition_failed",
      message: "Detector acquisition failed",
      phase: "execution",
      related_locations: [],
    },
    secondary: [
      {
        code: "abort_failed",
        message: "Instrument abort failed with unknown state",
        phase: "execution",
        related_locations: [],
      },
    ],
    terminal_persistence: "unconfirmed",
    diagnostics: [
      {
        generation: "a".repeat(32),
        request_id: 4,
        operation: "collect",
        instrument_id: "detector",
        retention: "retained",
        href: `/api/v1/instrument-workers/${"a".repeat(32)}/diagnostics`,
      },
    ],
    truncated: false,
  };
}

it("leads with the acquisition cause and links bounded evidence separately", () => {
  render(<RunFailureEvidenceContent evidence={evidence()} />);
  expect(screen.getByText("Detector acquisition failed")).toBeVisible();
  expect(screen.getByText("Instrument abort failed with unknown state")).not.toBeVisible();
  expect(screen.getByText(/Terminal persistence is unconfirmed/)).toBeVisible();
  expect(screen.getByRole("link", { name: /View retained diagnostics/ })).toHaveAttribute(
    "href",
    `/api/v1/instrument-workers/${"a".repeat(32)}/diagnostics`,
  );
  expect(screen.getByText(/bounded prefix/)).toBeVisible();
});

it("explains an exhausted retention quota without an unusable link", () => {
  const value = evidence();
  value.diagnostics = [
    { generation: "b".repeat(32), retention: "unavailable_active_quota", href: null },
  ];
  render(<RunFailureEvidenceContent evidence={value} />);
  expect(screen.getByText(/Diagnostics not retained/)).toBeVisible();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});
