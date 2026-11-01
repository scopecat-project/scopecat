// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { connectionOptionFields, OptionInput, optionsAreValid } from "./connection-fields";

afterEach(cleanup);
const schema = {
  type: "object",
  required: ["addresses", "mode"],
  properties: {
    addresses: {
      title: "控制器地址",
      type: "object",
      minProperties: 1,
      additionalProperties: { type: "string" },
    },
    python: { title: "Python 路径", anyOf: [{ type: "string" }, { type: "null" }], default: null },
    mode: { title: "连接方式", type: "string", enum: ["local", "bridge"] },
  },
};
it("edits named addresses without JSON and blocks incomplete or duplicate rows", () => {
  const field = connectionOptionFields(schema)[0]!;
  const change = vi.fn(),
    validity = vi.fn();
  render(
    <OptionInput
      field={field}
      value={{ main: "192.0.2.1" }}
      onChange={change}
      onValidityChange={validity}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "添加控制器地址" }));
  expect(validity).toHaveBeenLastCalledWith(false);
  expect(change).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("控制器地址 名称 2"), { target: { value: "main" } });
  fireEvent.change(screen.getByLabelText("控制器地址 值 2"), { target: { value: "192.0.2.2" } });
  expect(validity).toHaveBeenLastCalledWith(false);
  fireEvent.change(screen.getByLabelText("控制器地址 名称 2"), { target: { value: "aux" } });
  expect(validity).toHaveBeenLastCalledWith(true);
  expect(change).toHaveBeenLastCalledWith({ main: "192.0.2.1", aux: "192.0.2.2" });
  fireEvent.click(screen.getByRole("button", { name: "移除控制器地址第 1 行" }));
  expect(change).toHaveBeenLastCalledWith({ aux: "192.0.2.2" });
  fireEvent.click(screen.getByRole("button", { name: "移除控制器地址第 1 行" }));
  expect(validity).toHaveBeenLastCalledWith(false);
});
it("renders optional nullable paths and enums as ordinary controls", () => {
  const fields = connectionOptionFields(schema),
    change = vi.fn();
  render(
    <>
      <OptionInput field={fields[1]!} value={null} onChange={change} onValidityChange={vi.fn()} />
      <OptionInput field={fields[2]!} value="local" onChange={change} onValidityChange={vi.fn()} />
    </>,
  );
  fireEvent.change(screen.getByLabelText("Python 路径"), {
    target: { value: "/opt/vendor/python" },
  });
  expect(change).toHaveBeenLastCalledWith("/opt/vendor/python");
  fireEvent.change(screen.getByRole("combobox", { name: "连接方式" }), {
    target: { value: "bridge" },
  });
  expect(change).toHaveBeenLastCalledWith("bridge");
  expect(
    optionsAreValid({ addresses: { main: "192.0.2.1" }, python: null, mode: "local" }, fields),
  ).toBe(true);
  expect(optionsAreValid({ addresses: {}, mode: "invalid" }, fields)).toBe(false);
});
