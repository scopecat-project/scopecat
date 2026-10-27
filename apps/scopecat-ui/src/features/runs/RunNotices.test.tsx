// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RunAttentionNotice } from "./RunAttentionNotice";
import { RunCancellationNotice } from "./RunCancellationNotice";

afterEach(cleanup);

it("keeps raw attention reasons and does not imply closure makes hardware safe", () => {
  const close = vi.fn();
  const props = {
    run: {
      status: "attention_required" as const,
      attentionReason: "driver: unknown external effect",
    },
    onResolve: close,
    pending: false,
    error: null,
  };
  const view = render(<RunAttentionNotice {...props} />);
  expect(screen.getByRole("alert")).toHaveTextContent("driver: unknown external effect");
  expect(screen.getByText(/硬件效果未知时不能在此重试/)).toBeVisible();
  expect(screen.getByText(/关闭此记录不代表硬件已安全/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "关闭且不继续" }));
  expect(close).toHaveBeenCalledTimes(1);
  view.rerender(<RunAttentionNotice {...props} pending />);
  expect(screen.getByRole("button", { name: "正在关闭…" })).toBeDisabled();
});

it("keeps cancellation pending separate from completion or physical stop", () => {
  const view = render(
    <RunCancellationNotice
      run={{ status: "running", cancellationRequestedAt: "2026-10-27T00:00:00Z" }}
    />,
  );
  expect(screen.getByRole("status")).toHaveTextContent("取消尚未完成，也不表示设备已物理停止");
  view.rerender(
    <RunCancellationNotice
      run={{ status: "cancelled", cancellationRequestedAt: "2026-10-27T00:00:00Z" }}
    />,
  );
  expect(screen.queryByRole("status")).toBeNull();
});
