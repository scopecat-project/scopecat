// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MeasurementWorkspace } from "./MeasurementWorkspace";
import { RunAttentionNotice } from "./RunAttentionNotice";
import { RunFailureEvidenceContent } from "./RunFailureEvidence";
import { ResourceCard } from "./RunDetailSections";

const originals = new Map(
  ["showModal", "close"].map((name) => [
    name,
    Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, name),
  ]),
);
beforeEach(() => {
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: {
      configurable: true,
      value(this: HTMLDialogElement) {
        this.setAttribute("open", "");
      },
    },
    close: {
      configurable: true,
      value(this: HTMLDialogElement) {
        this.removeAttribute("open");
        this.dispatchEvent(new Event("close"));
      },
    },
  });
});
afterEach(() => {
  cleanup();
  for (const [name, descriptor] of originals) {
    if (descriptor) Object.defineProperty(HTMLDialogElement.prototype, name, descriptor);
    else Reflect.deleteProperty(HTMLDialogElement.prototype, name);
  }
});

it.each(["attention_required", "failed"] as const)(
  "keeps %s reasons inside focus and preserves selection when returning to run actions",
  (status) => {
    const resolve = vi.fn();
    function view(problem: boolean) {
      return (
        <MeasurementWorkspace
          title="Signal"
          notices={
            problem && (
              <>
                <RunAttentionNotice
                  run={{ status, attentionReason: "Cleanup state is unknown" }}
                  onResolve={resolve}
                  pending={false}
                  error={null}
                />
                <RunFailureEvidenceContent
                  evidence={{
                    primary: {
                      code: "compute_failed",
                      message: "Synthetic computation failed",
                      phase: "execution",
                      related_locations: [],
                    },
                    terminal_persistence: "unconfirmed",
                    secondary: [],
                    diagnostics: [],
                    truncated: false,
                  }}
                />
                <ResourceCard
                  run={{
                    resources: [
                      { id: "synthetic-resource", kind: "instrument", status: "quarantined" },
                    ],
                  }}
                />
              </>
            )
          }
        >
          <label>
            Slice
            <select defaultValue="0">
              <option value="0">First</option>
              <option value="1">Second</option>
            </select>
          </label>
        </MeasurementWorkspace>
      );
    }
    const { rerender } = render(view(false));
    fireEvent.change(screen.getByLabelText("Slice"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "聚焦曲线" }));
    rerender(view(true));
    const focused = within(screen.getByRole("dialog"));
    expect(focused.getByText("Synthetic computation failed")).toBeVisible();
    expect(focused.getByText(/Terminal persistence is unconfirmed/)).toBeVisible();
    expect(focused.getByText("Quarantined")).toBeVisible();
    expect(focused.queryAllByText("Cleanup state is unknown")).toHaveLength(
      status === "attention_required" ? 1 : 0,
    );
    expect(focused.queryAllByText(/does not establish that the hardware is safe/)).toHaveLength(
      status === "attention_required" ? 1 : 0,
    );
    expect(
      focused.queryByRole("button", { name: "Close without resuming" }),
    ).not.toBeInTheDocument();
    fireEvent.click(focused.getByRole("button", { name: "退出聚焦，查看 run 详情" }));
    expect(screen.getByLabelText("Slice")).toHaveValue("1");
    expect(screen.getByRole("button", { name: "聚焦曲线" })).toHaveFocus();
    expect(resolve).not.toHaveBeenCalled();
    expect(screen.queryAllByRole("button", { name: "Close without resuming" })).toHaveLength(
      status === "attention_required" ? 1 : 0,
    );
  },
);
