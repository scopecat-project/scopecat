// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { InstrumentDeviceState } from "../../api-contract";
import { DeviceStateCard, deviceStateTarget, stateMemberKey } from "./InstrumentInterfaceControls";

afterEach(cleanup);
const schema: InstrumentDeviceState = {
  id: "settings",
  label: "参数",
  members: [
    {
      component_path: ["channel"],
      property: {
        id: "points",
        label: "扫描点数",
        access: "read_write",
        capture: true,
        restore: true,
        description: "采集的采样数量",
        value_type: { type: "int" },
      },
    },
    {
      component_path: [],
      property: {
        id: "serial",
        label: "序列号",
        access: "read_only",
        capture: true,
        restore: false,
        value_type: { type: "string" },
      },
    },
  ],
};
it("stages the same semantic target and preserves reset and read-only behavior", () => {
  const onStage = vi.fn();
  const target = deviceStateTarget("settings", ["channel"], "points");
  render(
    <DeviceStateCard
      presentation="workbench"
      deviceState={schema}
      connected
      interactionDisabled={false}
      drafts={{ [stateMemberKey(target)]: { raw: "201", value: 201 } }}
      onStage={onStage}
    />,
  );
  const input = screen.getByRole("spinbutton");
  fireEvent.change(input, { target: { value: "401" } });
  expect(onStage).toHaveBeenCalledWith(target, { raw: "401", value: 401 });
  fireEvent.click(screen.getByRole("button", { name: "重置暂存值" }));
  expect(onStage).toHaveBeenLastCalledWith(target, undefined);
  expect(screen.getByRole("textbox")).toBeDisabled();
  expect(screen.getByText("采集的采样数量")).not.toBeVisible();
  fireEvent.click(screen.getByText("参数说明与观测"));
  expect(screen.getByText("采集的采样数量")).toBeVisible();
  expect(screen.getAllByText("暂无观测，当前设备值未知")).toHaveLength(2);
});
it("leaves the legacy surface unchanged unless explicitly selected", () => {
  render(
    <DeviceStateCard
      deviceState={schema}
      connected={false}
      interactionDisabled={false}
      drafts={{}}
      onStage={vi.fn()}
    />,
  );
  expect(screen.queryByText("参数说明与观测")).not.toBeInTheDocument();
  expect(screen.getByText("采集的采样数量")).toBeVisible();
  expect(screen.getByRole("spinbutton")).toBeDisabled();
  expect(screen.getByTestId("device-state-card-settings")).toHaveClass("device-interface-card");
});
it.each(["hardware_query", "command_confirmed", "configured_fixed", "derived"] as const)(
  "retains the observed value behind a draft and exposes %s provenance",
  (source) => {
    const target = deviceStateTarget("settings", ["channel"], "points");
    const labels = {
      hardware_query: "硬件查询",
      command_confirmed: "命令确认",
      configured_fixed: "配置固定值",
      derived: "推导值",
    };
    render(
      <DeviceStateCard
        presentation="workbench"
        deviceState={schema}
        connected
        interactionDisabled={false}
        drafts={{ [stateMemberKey(target)]: { raw: "401", value: 401 } }}
        state={{
          instrument_id: "test",
          observations: [{ target, value: 201, source, entity_ids: [], channel_bindings: [] }],
        }}
        onStage={vi.fn()}
      />,
    );
    expect(screen.getByRole("spinbutton")).toHaveValue(401);
    expect(screen.getByText("· 未写入")).toBeVisible();
    expect(screen.getByText(`${labels[source]} · 时间未知`)).toBeVisible();
    fireEvent.click(screen.getByText("参数说明与观测"));
    expect(screen.getByText(/上次观测值：201/)).toBeVisible();
  },
);
it("does not hide write-only semantics in the compact field", () => {
  const writeOnly: InstrumentDeviceState = {
    ...schema,
    members: [
      {
        ...schema.members![0],
        property: { ...schema.members![0]!.property, access: "write_only" },
      },
    ],
  };
  render(
    <DeviceStateCard
      presentation="workbench"
      deviceState={writeOnly}
      connected
      interactionDisabled={false}
      drafts={{}}
      onStage={vi.fn()}
    />,
  );
  expect(screen.getByText("只写")).toBeVisible();
});
