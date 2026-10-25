// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { ComparisonLink } from "./ComparisonLink";
afterEach(cleanup);
it("retains the exact editable draft and pinned task when comparing a different run", () => {
  window.history.replaceState(
    null,
    "",
    "/?draft=C&workspace=lab&experiment=signal&procedure=A#launch",
  );
  render(<ComparisonLink runId="run B & prior" />);
  fireEvent.click(screen.getByRole("link"));
  const url = new URL(window.location.href);
  expect(url.hash).toBe("#analyses");
  expect(Object.fromEntries(url.searchParams)).toEqual({
    compare: "run B & prior",
    procedure: "A",
    draft: "C",
    workspace: "lab",
    experiment: "signal",
  });
});
