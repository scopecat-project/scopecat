// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { SetupMappings, mappingProblems } from "./SetupMappings";
import type { SetupDefinition } from "./setup-api";

afterEach(cleanup);
const initial: SetupDefinition = {
  topology: { entities: [] },
  routing: {},
  domain_target: null,
  instruments: [
    {
      id: "source",
      device_id: "device-a",
      default_state: [],
      run_start: "preserve",
      success_action: "release",
      failure_action: "abort_and_release",
    },
    {
      id: "replacement",
      device_id: "device-b",
      default_state: [],
      run_start: "preserve",
      success_action: "release",
      failure_action: "abort_and_release",
    },
  ],
};
it("authors two qubit mappings from empty topology and replaces only one channel", () => {
  let current = initial;
  function Editor() {
    const [draft, setDraft] = useState(initial);
    return (
      <SetupMappings
        draft={draft}
        onChange={(next) => {
          current = next;
          setDraft(next);
        }}
      />
    );
  }
  render(<Editor />);
  for (const entity of ["q0", "q1"]) {
    fireEvent.change(screen.getByLabelText("对象 ID（如 q0）"), { target: { value: entity } });
    fireEvent.click(screen.getByRole("button", { name: "添加比特对象" }));
  }
  expect(screen.getByText("q0：尚未分配用途与设备通道")).toBeVisible();
  fireEvent.change(screen.getByLabelText("用途 ID（如 drive）"), { target: { value: "drive" } });
  fireEvent.click(screen.getByRole("button", { name: "添加用途" }));
  for (const entity of ["q0", "q1"]) {
    fireEvent.change(screen.getByLabelText("映射 ID"), { target: { value: `drive-${entity}` } });
    fireEvent.change(screen.getByLabelText("测量对象"), { target: { value: entity } });
    fireEvent.change(screen.getByLabelText("设备绑定"), { target: { value: "source" } });
    fireEvent.change(screen.getByLabelText("能力接口 ID（高级手填）"), {
      target: { value: "scopecat.rf_output/v2" },
    });
    fireEvent.change(screen.getByLabelText("设备组件路径（/ 分隔，可留空）"), {
      target: { value: `channels/${entity}` },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加映射" }));
  }
  const oldRevision = current;
  fireEvent.change(screen.getByLabelText("替换设备绑定 drive-q0"), {
    target: { value: "replacement" },
  });
  const path = screen.getByLabelText<HTMLInputElement>("替换组件路径 drive-q0 / 1");
  fireEvent.input(path, { target: { value: "" } });
  let typed = "";
  for (const character of "channels/2/I") {
    typed += character;
    // Use the current DOM value for each keystroke, not a complete pasted path.
    fireEvent.input(path, { target: { value: path.value + character } });
    expect(path).toHaveValue(typed);
  }
  expect(current.routing.routes?.[0]?.endpoints[0]?.component_path).toEqual(["channels", "2", "I"]);
  expect(current.routing.routes?.[0]?.instrument_id).toBe("replacement");
  expect(current.routing.routes?.[1]).toEqual(oldRevision.routing.routes?.[1]);
  expect(oldRevision.routing.routes?.[0]?.instrument_id).toBe("source");
  expect(initial.topology.entities).toEqual([]);
  expect(mappingProblems(current)).toEqual([]);
});
it("reports missing references and ambiguous candidates without guessing a route", () => {
  const route = {
    id: "a",
    instrument_id: "missing-device",
    role_id: "drive",
    entity_ids: ["q0"],
    endpoints: [{ interface_id: "scopecat.rf_output/v2", component_path: [] }],
  };
  const problems = mappingProblems({
    ...initial,
    topology: { entities: [{ id: "q0" }] },
    routing: { roles: [{ id: "drive" }], routes: [route, { ...route, id: "b" }] },
  });
  expect(problems.some((item) => item.includes("缺少设备绑定"))).toBe(true);
  expect(problems.some((item) => item.includes("多个候选"))).toBe(true);
});

const dcDeclaration = {
  description: {
    instrument_id: "device-a",
    implementation_id: "dual-dc",
    implementation_version: "1",
    interfaces: [{ id: "scopecat.dc_bias/v1" }],
    interface_mounts: ["ch1", "ch2"].map((channel) => ({
      interface_id: "scopecat.dc_bias/v1",
      component_path: ["channels", channel],
    })),
  },
};
const mapped: SetupDefinition = {
  ...initial,
  topology: { entities: [{ id: "q0" }] },
  routing: {
    roles: [{ id: "bias" }],
    routes: [
      {
        id: "bias-q0",
        instrument_id: "source",
        role_id: "bias",
        entity_ids: ["q0"],
        endpoints: [
          {
            interface_id: "scopecat.dc_bias/v1",
            component_path: ["channels", "ch1"],
            channel_id: "logical-bias",
          },
        ],
      },
    ],
  },
};
it("selects ch2 from declarations without changing logical channels or the old setup", () => {
  let current = mapped;
  function Editor() {
    const [draft, setDraft] = useState(mapped);
    return (
      <SetupMappings
        draft={draft}
        declarations={{ source: dcDeclaration }}
        onChange={(next) => {
          current = next;
          setDraft(next);
        }}
      />
    );
  }
  render(<Editor />);
  fireEvent.change(screen.getByLabelText("已声明能力与端点 bias-q0 / 1"), {
    target: { value: "1" },
  });
  expect(current.routing.routes?.[0]?.endpoints[0]).toEqual({
    interface_id: "scopecat.dc_bias/v1",
    component_path: ["channels", "ch2"],
    channel_id: "logical-bias",
  });
  expect(mapped.routing.routes?.[0]?.endpoints[0]?.component_path).toEqual(["channels", "ch1"]);
  expect(screen.queryByText(/组件路径不存在/)).not.toBeInTheDocument();
});
it("explains declaration mismatches and clears them when the endpoint is corrected", () => {
  const original = structuredClone(mapped);
  function Editor() {
    const [draft, setDraft] = useState(original);
    return (
      <SetupMappings draft={draft} declarations={{ source: dcDeclaration }} onChange={setDraft} />
    );
  }
  render(<Editor />);
  fireEvent.change(screen.getByLabelText("替换组件路径 bias-q0 / 1"), {
    target: { value: "channels/ch9" },
  });
  expect(screen.getByText(/组件路径不存在于该能力声明/)).toHaveTextContent(
    "channels/ch1、channels/ch2",
  );
  fireEvent.change(screen.getByLabelText("能力接口 ID bias-q0 / 1"), {
    target: { value: "scopecat.dc_bias/v99" },
  });
  expect(screen.getByText(/能力版本不匹配/)).toHaveTextContent("scopecat.dc_bias/v1");
  fireEvent.change(screen.getByLabelText("已声明能力与端点 bias-q0 / 1"), {
    target: { value: "1" },
  });
  expect(screen.queryByText(/能力版本不匹配/)).not.toBeInTheDocument();
  expect(screen.queryByText(/组件路径不存在/)).not.toBeInTheDocument();
});
it("keeps unknown declarations explicit and preserves manually entered mappings", () => {
  let current = mapped;
  render(
    <SetupMappings
      draft={mapped}
      onChange={(next) => {
        current = next;
      }}
    />,
  );
  expect(screen.getAllByText(/声明未知：无法核对/).length).toBeGreaterThan(0);
  expect(screen.getByLabelText("已声明能力与端点 bias-q0 / 1").children).toHaveLength(1);
  fireEvent.change(screen.getByLabelText("替换组件路径 bias-q0 / 1"), {
    target: { value: "custom/unknown" },
  });
  expect(current.routing.routes?.[0]?.endpoints[0]?.component_path).toEqual(["custom", "unknown"]);
  expect(screen.getByText(/保存允许未完成配置/)).toBeVisible();
});
