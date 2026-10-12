// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { ReturnToPreparation } from "./ReturnToPreparation";

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

it("returns in place with procedure context while leaving result selections behind", () => {
  window.history.replaceState(null, "", "/?procedure=p1&run=r1&run-analysis=a1#runs");
  render(<ReturnToPreparation />);
  const link = screen.getByRole("link", { name: "Return to preparation" });
  expect(link).toHaveAttribute("href", "?preparation=1&procedure=p1#launch");
  expect(fireEvent.click(link, { ctrlKey: true })).toBe(true);
  expect(window.location.hash).toBe("#runs");
  expect(fireEvent.click(link)).toBe(false);
  expect(window.location.search).toBe("?preparation=1&procedure=p1");
  expect(window.location.hash).toBe("#launch");
});

it("also offers the existing preparation from a run opened without procedure context", () => {
  window.history.replaceState(null, "", "/?run=r1#runs");
  render(<ReturnToPreparation />);
  expect(screen.getByRole("link", { name: "Return to preparation" })).toHaveAttribute(
    "href",
    "?preparation=1#launch",
  );
});
