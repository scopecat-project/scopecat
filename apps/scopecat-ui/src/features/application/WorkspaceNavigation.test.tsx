// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceNavigation } from "./WorkspaceNavigation";
afterEach(cleanup);
it("keeps decision attention visible when compact and reports unknown without implying zero", () => {
  const props = {
    view: "launch" as const,
    onSelect: vi.fn(),
    focused: false,
    onShowNavigation: vi.fn(),
  };
  const { rerender } = render(<WorkspaceNavigation {...props} decisionAttention="3 待处理" />);
  fireEvent.click(screen.getByRole("button", { name: /Collapse navigation$/ }));
  expect(screen.getByLabelText("Decision attention")).toHaveTextContent("3 待处理");
  rerender(<WorkspaceNavigation {...props} decisionAttention="状态未知" />);
  expect(screen.getByLabelText("Decision attention")).toHaveTextContent("状态未知");
});
it("keeps labeled destinations in compact mode and restores focus after the drawer closes", () => {
  vi.stubGlobal("innerWidth", 520);
  const select = vi.fn();
  render(
    <>
      <WorkspaceNavigation
        view="launch"
        onSelect={select}
        focused={false}
        onShowNavigation={() => {}}
      />
      <main id="workspace-body">Editor</main>
    </>,
  );
  fireEvent.click(screen.getByRole("button", { name: /Collapse navigation$/ }));
  expect(document.documentElement).toHaveClass("navigation-compact");
  expect(screen.getByRole("button", { name: /Configuration$/ })).toHaveTextContent("参数与绑定");
  const open = screen.getByRole("button", { name: /Open navigation$/ });
  fireEvent.click(open);
  expect(document.getElementById("workspace-body")?.inert).toBe(true);
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(document.getElementById("workspace-body")?.inert).toBe(false);
  expect(open).toHaveFocus();
  fireEvent.click(screen.getByRole("button", { name: /Devices and drivers$/ }));
  expect(select).toHaveBeenCalledWith("instruments");
  vi.unstubAllGlobals();
});
it("does not repeat navigation in a focused object window", () => {
  render(
    <WorkspaceNavigation view="runs" onSelect={() => {}} focused onShowNavigation={() => {}} />,
  );
  expect(screen.queryByRole("navigation")).toBeNull();
  expect(screen.getByRole("button", { name: /Navigation/ })).toBeVisible();
});
