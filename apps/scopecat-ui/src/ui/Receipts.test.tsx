// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReceiptProvider, showReceipt } from "./Receipts";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("shows explicit receipts outside the page flow and allows dismissal", async () => {
  render(
    <ReceiptProvider>
      <p role="alert">保存失败，请重试</p>
    </ReceiptProvider>,
  );
  expect(screen.queryByText("已复制")).toBeNull();
  act(() => {
    showReceipt("已复制");
  });
  expect(screen.getByText("已复制")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "关闭通知" }));
  expect(screen.queryByText("已复制")).toBeNull();
  expect(screen.getByRole("alert")).toHaveTextContent("保存失败，请重试");
});

it("expires short receipts without removing persistent owner errors", async () => {
  vi.useFakeTimers();
  render(
    <ReceiptProvider>
      <p role="alert">结果未知</p>
    </ReceiptProvider>,
  );
  act(() => {
    showReceipt("已复制");
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(4000);
  });
  expect(screen.queryByText("已复制")).toBeNull();
  expect(screen.getByRole("alert")).toHaveTextContent("结果未知");
});
