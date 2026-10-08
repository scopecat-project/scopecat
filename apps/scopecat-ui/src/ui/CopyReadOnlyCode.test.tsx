// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { readOnlyCode, type ReadOnlyCopyTarget } from "../lib/read-only-code";
import { CopyReadOnlyCode } from "./CopyReadOnlyCode";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const first: ReadOnlyCopyTarget = { kind: "run", runId: "run-a", publicationId: "analysis-fit-r1" };
function clipboard(writeText?: (code: string) => Promise<void>) {
  vi.spyOn(navigator, "clipboard", "get").mockReturnValue(
    writeText ? ({ writeText } as Clipboard) : (undefined as unknown as Clipboard),
  );
}
// jsdom does not provide the Clipboard API.
Object.defineProperty(navigator, "clipboard", { configurable: true, get: () => undefined });

it("writes only on click and reports success only after the write resolves", async () => {
  let complete!: () => void;
  const write = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        complete = resolve;
      }),
  );
  clipboard(write);
  render(<CopyReadOnlyCode target={first} />);
  expect(write).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button"));
  expect(write).toHaveBeenCalledWith(readOnlyCode(first));
  expect(screen.queryByText("Read-only code copied.")).toBeNull();
  expect(screen.getByRole("button")).toBeDisabled();
  await act(async () => complete());
  expect(screen.getByRole("status")).toHaveTextContent("Read-only code copied.");
});

it.each(["missing", "denied", "error", "sync error"])(
  "offers the identical selectable code on %s",
  async (failure) => {
    clipboard(
      failure === "missing"
        ? undefined
        : failure === "sync error"
          ? () => {
              throw new Error("failed");
            }
          : () =>
              Promise.reject(
                failure === "denied"
                  ? new DOMException("Denied", "NotAllowedError")
                  : new Error("failed"),
              ),
    );
    render(<CopyReadOnlyCode target={first} />);
    fireEvent.click(screen.getByRole("button"));
    const field = await screen.findByRole("textbox", { name: "Read-only code" });
    expect(field).toHaveValue(readOnlyCode(first));
    fireEvent.focus(field);
    expect((field as HTMLTextAreaElement).selectionStart).toBe(0);
    expect((field as HTMLTextAreaElement).selectionEnd).toBe(readOnlyCode(first).length);
    expect(screen.queryByText("Read-only code copied.")).toBeNull();
  },
);

it.each([true, false])(
  "ignores stale clipboard completion across run/publication/owner changes (success=%s)",
  async (success) => {
    let complete!: () => void;
    clipboard(
      () =>
        new Promise<void>((resolve, reject) => {
          complete = success ? resolve : () => reject(new Error("denied"));
        }),
    );
    const { rerender } = render(<CopyReadOnlyCode target={first} />);
    for (const target of [
      { ...first, runId: "run-b" },
      { ...first, publicationId: "analysis-fit-r2" },
      { kind: "project" as const, publicationId: "analysis-fit-r1" },
    ]) {
      fireEvent.click(screen.getByRole("button"));
      rerender(<CopyReadOnlyCode target={target} />);
      await act(async () => complete());
      expect(screen.queryByRole("status")).toBeNull();
      expect(screen.getByRole("button")).toBeEnabled();
    }
  },
);

it("clears pending feedback when detail is replaced by loading/error and reopened", async () => {
  let complete!: () => void;
  clipboard(
    () =>
      new Promise<void>((resolve) => {
        complete = resolve;
      }),
  );
  const { rerender } = render(<CopyReadOnlyCode target={first} />);
  fireEvent.click(screen.getByRole("button"));
  rerender(<p>Loading detail</p>);
  await act(async () => complete());
  rerender(<p>Detail unavailable</p>);
  expect(screen.queryByRole("button")).toBeNull();
  rerender(<CopyReadOnlyCode target={first} />);
  expect(screen.queryByRole("status")).toBeNull();
});
