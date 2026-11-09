// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { CollectResultData } from "./CollectResultData";
import { decodeCollectReceipt } from "./hardware-receipt-wire";
import { producerContent } from "./receipt-fixture.test-support";
afterEach(cleanup);
it("lazily exposes every exact complex sample with availability and bounded pages", () => {
  render(
    <CollectResultData
      receipt={{
        status: "collected",
        problems: [],
        readback: {
          values: {
            signal: {
              kind: "array",
              dtype: "complex128",
              shape: [101],
              unit: "ratio",
              values: Array.from({ length: 101 }, (_, index) => ({
                real: index + 0.123456789,
                imag: -index,
              })),
              availability: {
                valid: Array.from({ length: 101 }, (_, index) => index !== 100),
                unavailable: [],
              },
            },
          },
        },
      }}
    />,
  );
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  const details = screen.getByText("结果数据（数值与可用性）").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  expect(screen.getByText('{"real":0.123456789,"imag":0}')).toBeVisible();
  expect(screen.getAllByRole("row")).toHaveLength(101);
  fireEvent.click(screen.getByRole("button", { name: "下一页数据" }));
  expect(screen.getByText('{"real":100.123456789,"imag":-100}')).toBeVisible();
  expect(screen.getByText("不可用（原因见完整回执）")).toBeVisible();
  expect(screen.getByRole("button", { name: "下一页数据" })).toBeDisabled();
  expect(screen.getAllByRole("row")).toHaveLength(2);
});

it("does not claim unsafe int64 values are exact", () => {
  render(
    <CollectResultData
      receipt={{
        status: "collected",
        problems: [],
        readback: {
          values: {
            counter: { kind: "array", dtype: "int64", shape: [1], values: [9007199254740992] },
          },
        },
      }}
    />,
  );
  const details = screen.getByText("结果数据（数值与可用性）").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  expect(screen.getByText(/不是这些大整数的精确原值/)).toBeVisible();
  expect(screen.getByText("近似值 9007199254740992（精度无法保证）")).toBeVisible();
});

it("warns for large integers decoded from the real Python HTTP receipt", () => {
  render(<CollectResultData receipt={decodeCollectReceipt(producerContent())} />);
  const details = screen.getByText("结果数据（数值与可用性）").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  fireEvent.change(screen.getByRole("combobox", { name: "精确结果字段" }), {
    target: { value: "int_array" },
  });
  expect(screen.getAllByText(/近似值.*精度无法保证/)).toHaveLength(2);
  expect(screen.getByText(/不是这些大整数的精确原值/)).toBeVisible();
  fireEvent.change(screen.getByRole("combobox", { name: "精确结果字段" }), {
    target: { value: "int_max" },
  });
  expect(screen.getByText(/不是这些大整数的精确原值/)).toBeVisible();
});
