// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { CalibrationAssessments } from "./CalibrationAssessments";

test("explains changed scientific dependencies and links retained evidence", () => {
  render(
    <CalibrationAssessments
      assessments={[
        {
          run_id: "run-a",
          status: "recheck",
          reasons: ["dependency_inputs_changed"],
          dependencies: {
            status: "changed",
            reasons: ["dependency_values_changed"],
            compared_parameters: ["bias", "readout"],
            changed_parameters: ["bias"],
            row_comparisons: [
              {
                selector: {
                  parameter: "bias",
                  equals: [{ id: "group", shape: "scalar", value: "read" }],
                },
                status: "changed",
                reason: "selection_membership_changed",
                before_count: 1,
                after_count: 2,
              },
            ],
          },
        },
      ]}
    />,
  );
  expect(screen.getByText("已改变参数： bias")).toBeInTheDocument();
  expect(screen.getByText(/bias \[group="read"\]/)).toHaveTextContent(
    "选择成员已变化。 匹配行数：1 → 2",
  );
  expect(screen.getByRole("link", { name: "run-a", hidden: true })).toHaveAttribute(
    "href",
    "?run=run-a#runs",
  );
});
