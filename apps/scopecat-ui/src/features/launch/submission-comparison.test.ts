import { expect, it } from "vitest";
import { compareSubmission } from "./submission-comparison";
import { comparisonFixture } from "./submission-comparison.test-support";
import { defaultSelection } from "./scientific-selection";

it("compares parsed fields and equivalent units without changing either input", () => {
  const { request, draft } = comparisonFixture();
  const original = JSON.stringify({ request, draft });
  const rows = compareSubmission(request, draft);
  for (const label of ["输入 · count", "输入 · enabled", "输入 · labels", "控制项 · frequency"])
    expect(rows.find((row) => row.label === label)?.status).toBe("Same");
  expect(JSON.stringify({ request, draft })).toBe(original);
});
it.each(["", " ", "bad", "NaN", "2.5", "0", "0x10"])(
  "marks invalid current numeric input %j incomplete",
  (value) => {
    const { request, draft } = comparisonFixture();
    draft.values.count = value;
    expect(
      compareSubmission(request, draft).find((row) => row.label === "输入 · count")?.status,
    ).toBe("Incomplete current input");
  },
);
it("never reconstructs omitted submitted inputs from current defaults", () => {
  const { request, draft } = comparisonFixture();
  request.inputs = {};
  request.control_edits = {};
  const rows = compareSubmission(request, draft);
  expect(rows.find((row) => row.label === "输入 · count")).toMatchObject({
    submitted: "提交时默认值",
    current: "2.0",
    status: "Not compared",
  });
  expect(rows.find((row) => row.label === "控制项 · frequency")).toMatchObject({
    submitted: "提交时默认值",
    status: "Not compared",
  });
});
it("identifies changed source, configuration identity and scan order", () => {
  const { request, draft } = comparisonFixture();
  draft.sourceBaseline = { content_hash: "source-b" };
  request.selection = {
    ...defaultSelection(),
    configuration: {
      kind: "parameters",
      ref: { revision_id: "p1", content_hash: "hash-a" },
      overrides: [],
    },
  };
  draft.selection = {
    ...defaultSelection(),
    configuration: {
      kind: "parameters",
      ref: { revision_id: "p1", content_hash: "hash-b" },
      overrides: [],
    },
  };
  request.control_edits = { frequency: { mode: "scan", axis: { kind: "values", values: [1, 2] } } };
  draft.controls.frequency = { mode: "values", values: "2, 1", unit: null };
  for (const label of ["源码修订", "配置", "控制项 · frequency"])
    expect(compareSubmission(request, draft).find((row) => row.label === label)?.status).toBe(
      "Changed",
    );
});
it("keeps invalid scans and declaration loss explicit", () => {
  const { request, draft } = comparisonFixture();
  draft.controls.frequency = { mode: "range", start: "1", stop: "", points: "2", unit: "Hz" };
  draft.definition = "unavailable";
  const rows = compareSubmission(request, draft);
  expect(rows.find((row) => row.label === "控制项 · frequency")?.status).toBe(
    "Incomplete current input",
  );
  expect(rows.find((row) => row.label === "输入 · count")?.status).toBe("Not compared");
});

it("ignores inactive serialized control fields and does not claim a working table is frozen", () => {
  const { request, draft } = comparisonFixture();
  request.control_edits!.frequency!.axis = null;
  draft.workingInput = { draft_id: "table-b", revision: 9 };
  const rows = compareSubmission(request, draft);
  expect(rows.find((row) => row.label === "控制项 · frequency")?.status).toBe("Same");
  expect(rows.find((row) => row.label === "工作参数表")).toMatchObject({
    status: "Not compared",
    current: "工作表修订 9 · 需要预览与冻结",
  });
});

it("shows changed parameter values rather than only edit counts", () => {
  const { request, draft } = comparisonFixture();
  const config = {
    kind: "parameters" as const,
    ref: { revision_id: "p1", content_hash: "hash-a" },
    overrides: [
      {
        kind: "replace_parameter" as const,
        value: { id: "gain", shape: "scalar" as const, value: 1 },
      },
    ],
  };
  request.selection = { ...defaultSelection(), configuration: config };
  draft.selection = {
    ...defaultSelection(),
    configuration: {
      ...config,
      overrides: [{ kind: "replace_parameter", value: { id: "gain", shape: "scalar", value: 2 } }],
    },
  };
  expect(compareSubmission(request, draft).find((row) => row.label === "参数改动")).toMatchObject({
    status: "Changed",
    submitted: "gain: 1",
    current: "gain: 2",
  });
});
