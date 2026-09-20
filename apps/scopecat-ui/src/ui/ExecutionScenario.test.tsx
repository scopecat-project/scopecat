// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { scenarioFixture } from "../test/scenario-fixture";
import { ExecutionScenario } from "./ExecutionScenario";

afterEach(cleanup);
it("shows declared software model coverage and limitations", () => {
  render(<ExecutionScenario scenario={scenarioFixture} label="Retained execution scenario" />);
  expect(screen.getByRole("region", { name: "Retained execution scenario" })).toBeVisible();
  expect(screen.getByText("Synthetic resonance")).toBeVisible();
  expect(screen.getByText("reference.resonance")).toBeVisible();
  expect(screen.getByText(/Seed: 17/)).toBeVisible();
  expect(screen.getByText("Resonance frequency sweep")).toBeVisible();
  expect(screen.getByText("Does not model device heating")).toBeVisible();
});
it("does not infer physical execution when no scenario is declared", () => {
  render(<ExecutionScenario label="Reviewed execution scenario" />);
  expect(screen.getByText("No execution scenario declared.")).toBeVisible();
  expect(screen.queryByText(/physical/i)).not.toBeInTheDocument();
});
