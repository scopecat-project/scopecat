// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  initialConnection,
  newConnection,
  connectionOptionFields,
  OptionInput,
  optionsAreValid,
} from "./connection-fields";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
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

it("preserves legacy options and only applies defaults to a new connection", () => {
  const optionsSchema = {
    type: "object",
    required: ["master"],
    properties: {
      master: { type: "string" },
      source: { type: "string", enum: ["device", "file"], default: "device" },
      python: { anyOf: [{ type: "string" }, { type: "null" }], default: null },
      root: { type: "string" },
    },
    if: { properties: { source: { const: "device" } }, required: ["source"] },
    // JSON Schema keyword, not a Promise method.
    // eslint-disable-next-line unicorn/no-thenable
    then: { required: ["root"] },
  };
  const spec = { kind: "driver_managed" as const, options_schema: optionsSchema };
  const driver = {
    driver_id: "test",
    implementation_version: "1",
    label: "Test",
    connections: [spec],
  };
  const legacy = { kind: "driver_managed" as const, options: { master: "main" } };
  expect(initialConnection(legacy, driver)).toEqual(legacy);
  expect(
    optionsAreValid(legacy.options, connectionOptionFields(optionsSchema, legacy.options)),
  ).toBe(true);
  expect(newConnection("driver_managed", spec).options).toEqual({ source: "device", python: null });
  const migrated = { ...legacy.options, source: "device" };
  expect(optionsAreValid(migrated, connectionOptionFields(optionsSchema, migrated))).toBe(false);
});

it("selects a native path only on success and preserves the draft on cancellation", async () => {
  const choose = vi.fn().mockResolvedValueOnce(null).mockResolvedValueOnce("C:/SDK with spaces");
  vi.stubGlobal("pywebview", { api: { choose_directory: choose } });
  const field = connectionOptionFields({
    properties: { root: { type: "string", title: "SDK 目录", format: "directory-path" } },
  })[0]!;
  const change = vi.fn(),
    validity = vi.fn();
  render(
    <OptionInput field={field} value="C:/old" onChange={change} onValidityChange={validity} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "选择SDK 目录" }));
  expect(screen.getByRole("button", { name: "正在选择…" })).toBeDisabled();
  await waitFor(() => expect(validity).toHaveBeenLastCalledWith(true));
  expect(change).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "选择SDK 目录" }));
  await waitFor(() => expect(change).toHaveBeenLastCalledWith("C:/SDK with spaces"));
});

it("renders a required enum behind a local reference and validates its choice", () => {
  const fields = connectionOptionFields({
    $defs: { Mode: { type: "string", enum: ["local", "remote"] } },
    type: "object",
    required: ["mode"],
    properties: { mode: { $ref: "#/$defs/Mode", title: "时钟来源" } },
  });
  expect(fields).toHaveLength(1);
  expect(optionsAreValid({}, fields)).toBe(false);
  expect(optionsAreValid({ mode: "local" }, fields)).toBe(true);
  expect(optionsAreValid({ mode: "other" }, fields)).toBe(false);
  const change = vi.fn();
  render(
    <OptionInput
      field={fields[0]!}
      value={undefined}
      onChange={change}
      onValidityChange={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByRole("combobox", { name: "时钟来源" }), {
    target: { value: "remote" },
  });
  expect(change).toHaveBeenCalledWith("remote");
});

it("retains unsupported required fields and rejects cyclic or remote references without fetching", () => {
  for (const ref of ["#/$defs/Cycle", "https://example.invalid/schema"]) {
    const fields = connectionOptionFields({
      $defs: { Cycle: { $ref: "#/$defs/Cycle" } },
      required: ["mode"],
      properties: { mode: { $ref: ref } },
    });
    expect(fields).toHaveLength(1);
    expect(fields[0]?.type).toBe("unsupported");
    expect(optionsAreValid({}, fields)).toBe(false);
  }
});

it("honors exclusive numeric limits from referenced nullable schemas", () => {
  const fields = connectionOptionFields({
    $defs: { Rate: { type: "number", exclusiveMinimum: 0 } },
    properties: { rate: { anyOf: [{ $ref: "#/$defs/Rate" }, { type: "null" }], default: null } },
  });
  expect(optionsAreValid({ rate: 0 }, fields)).toBe(false);
  expect(optionsAreValid({ rate: 1 }, fields)).toBe(true);
});
