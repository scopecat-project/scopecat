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
  expect(screen.getByText(/尚未确认终态是否已持久保存/)).toBeVisible();
  expect(screen.getByRole("link", { name: /查看已保留的诊断/ })).toHaveAttribute(
    "href",
    `/api/v1/instrument-workers/${"a".repeat(32)}/diagnostics`,
  );
  expect(screen.getByText(/长度受限的开头部分/)).toBeVisible();
});

it("explains an exhausted retention quota without an unusable link", () => {
  const value = evidence();
  value.diagnostics = [
    { generation: "b".repeat(32), retention: "unavailable_active_quota", href: null },
  ];
  render(<RunFailureEvidenceContent evidence={value} />);
  expect(screen.getByText(/诊断未保留/)).toBeVisible();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});

it("marks truncated evidence and keeps an absent primary failure absent", () => {
  const value = evidence();
  value.truncated = true;
  const view = render(<RunFailureEvidenceContent evidence={value} />);
  expect(screen.getByText("仅显示最近保存的部分证据。")).toBeVisible();
  expect(screen.getByText("其他失败证据（1）")).toBeVisible();
  view.rerender(<RunFailureEvidenceContent evidence={{ ...value, primary: null }} />);
  expect(screen.queryByRole("region", { name: "运行失败证据" })).toBeNull();
});
