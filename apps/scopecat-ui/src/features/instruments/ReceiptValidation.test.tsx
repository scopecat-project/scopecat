// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CurrentMeasurement, type CurrentMeasurementResult } from "./CurrentMeasurement";
import { OriginalReceiptDownload, ReceiptValidationNotice } from "./ReceiptValidation";
vi.mock("./BoundedMeasurement", () => ({ BoundedMeasurement: () => <div>preview mounted</div> }));
afterEach(cleanup);
const result: CurrentMeasurementResult = {
  target: { interfaceId: "scope", componentPath: [], acquisition: { id: "capture", results: [] } },
  selection: {
    session_id: "closed",
    instrument_id: "scope",
    command_id: "original",
    title: "",
    series_id: "",
    line_id: "",
    line_label: "",
    note: "",
  },
  receivedAt: "2026-01-01T00:00:00Z",
};
it.each([
  ["unverified", "未验证原件（验证未完成）"],
  ["invalid", "无效原件（契约不匹配）"],
] as const)(
  "preserves %s identity and diagnostic reason without mounting any preview",
  (receipt_validation, label) => {
    render(
      <CurrentMeasurement
        result={{
          ...result,
          receipt_validation,
          validation_reason: "bounded reason",
          validation_code: "diagnostic",
        }}
        staged={false}
      />,
    );
    expect(screen.getByText(label)).toBeVisible();
    expect(screen.getByText("bounded reason")).toBeVisible();
    expect(screen.getByText(/保存不表示它是合格科学数据/)).toBeVisible();
    expect(screen.queryByText("preview mounted")).not.toBeInTheDocument();
  },
);
it("keeps legacy verified records viewable and downloads originals through a native link", () => {
  render(
    <>
      <CurrentMeasurement result={result} staged={false} />
      <ReceiptValidationNotice value={{ receipt_validation: "verified" }} />
      <OriginalReceiptDownload recordId="raw record" />
    </>,
  );
  expect(screen.getByText("preview mounted")).toBeVisible();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  const link = screen.getByRole("link", { name: "下载原始回执（非预览）" });
  expect(link).toHaveAttribute(
    "href",
    `${location.origin}/api/v1/direct-measurements/raw%20record/receipt`,
  );
  expect(link).toHaveAttribute("download", "raw record.hardware-receipt");
});
