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
  const link = screen.getByRole("link", { name: "返回准备" });
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
  expect(screen.getByRole("link", { name: "返回准备" })).toHaveAttribute(
    "href",
    "?preparation=1#launch",
  );
});

it("focuses preparation again when its return URL is already selected", () => {
  window.history.replaceState(null, "", "/?preparation=1&procedure=p1#launch");
  render(
    <>
      <ReturnToPreparation />
      <section tabIndex={-1} aria-labelledby="experiment-preparation">
        <h3 id="experiment-preparation">准备实验</h3>
      </section>
    </>,
  );
  const preparation = screen.getByRole("region", { name: "准备实验" });
  const link = screen.getByRole("link", { name: "返回准备" });
  fireEvent.click(link, { ctrlKey: true });
  expect(preparation).not.toHaveFocus();
  fireEvent.click(link);
  expect(preparation).toHaveFocus();
});

it("retains the exact editing draft through a monitor return and page reload", () => {
  const monitor = "/?procedure=A&run=child&workspace=author&experiment=signal&draft=B#runs";
  window.history.replaceState(null, "", monitor);
  const view = render(<ReturnToPreparation />);
  fireEvent.click(screen.getByRole("link", { name: "返回准备" }));
  expect(Object.fromEntries(new URL(window.location.href).searchParams)).toEqual({
    preparation: "1",
    procedure: "A",
    workspace: "author",
    experiment: "signal",
    draft: "B",
  });
  const reopened = window.location.href;
  view.unmount();
  window.history.replaceState(null, "", reopened);
  render(<ReturnToPreparation />);
  expect(screen.getByRole("link", { name: "返回准备" })).toHaveAttribute(
    "href",
    "?preparation=1&procedure=A&workspace=author&experiment=signal&draft=B#launch",
  );
});
