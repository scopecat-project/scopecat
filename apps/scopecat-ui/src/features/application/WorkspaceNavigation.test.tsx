// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceNavigation } from "./WorkspaceNavigation";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("releases the drawer on focused transition and unmount without focusing detached controls", async () => {
  vi.stubGlobal("innerWidth", 520);
  const props = { view: "runs" as const, onSelect: vi.fn(), onShowNavigation: vi.fn() };
  const content = (focused: boolean, show = true) => (
    <>
      {show && <WorkspaceNavigation {...props} focused={focused} />}
      <main id="workspace-body">
        <button>Run action</button>
      </main>
    </>
  );
  const { rerender } = render(content(false));
  fireEvent.click(screen.getByRole("button", { name: /Open navigation$/ }));
  const detachedPanel = screen.getByRole("dialog");
  rerender(content(true));
  expect(document.getElementById("workspace-body")?.inert).toBe(false);
  await waitFor(() => expect(screen.getByRole("button", { name: /Navigation/ })).toHaveFocus());
  const tab = new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true });
  detachedPanel.dispatchEvent(tab);
  expect(tab.defaultPrevented).toBe(false);
  rerender(content(false));
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Open navigation$/ }));
  vi.stubGlobal("innerWidth", 1266);
  fireEvent(window, new Event("resize"));
  expect(document.getElementById("workspace-body")?.inert).toBe(false);
  await waitFor(() => expect(screen.getByRole("button", { name: /Runs$/ })).toHaveFocus());
  vi.stubGlobal("innerWidth", 520);
  fireEvent.click(screen.getByRole("button", { name: /Open navigation$/ }));
  rerender(content(false, false));
  expect(document.getElementById("workspace-body")?.inert).toBe(false);
  await waitFor(() => expect(screen.getByRole("button", { name: "Run action" })).toHaveFocus());
});
it("keeps decision attention visible when compact and reports unknown without implying zero", () => {
  const props = {
    view: "launch" as const,
    onSelect: vi.fn(),
    focused: false,
    onShowNavigation: vi.fn(),
  };
  const { rerender } = render(<WorkspaceNavigation {...props} decisionAttention="3 待处理" />);
  fireEvent.click(screen.getByRole("button", { name: /Collapse navigation$/ }));
  expect(screen.getByLabelText("待处理判断")).toHaveTextContent("3 待处理");
  rerender(<WorkspaceNavigation {...props} decisionAttention="状态未知" />);
  expect(screen.getByLabelText("待处理判断")).toHaveTextContent("状态未知");
});
it("keeps labeled destinations in compact mode and restores focus after the drawer closes", async () => {
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
  await waitFor(() => expect(open).toHaveFocus());
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
