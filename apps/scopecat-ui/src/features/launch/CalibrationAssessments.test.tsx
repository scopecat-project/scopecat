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
          },
        },
      ]}
    />,
  );
  expect(screen.getByText("Changed parameters: bias")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "run-a", hidden: true })).toHaveAttribute(
    "href",
    "?run=run-a#runs",
  );
});
