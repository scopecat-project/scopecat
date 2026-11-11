// @vitest-environment jsdom
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

// Exercise the exact signed/package probe source, not a second implementation.
const source = readFileSync(
  resolve("../../packages/scopecat-app/src/scopecat_app/trial_window_probe.py"),
  "utf8",
);
const template = source.match(/FIELD_ASSIGNMENT = r"""([\s\S]*?)"""/)?.[1];
if (!template) throw new Error("Packaged probe field assignment not found");
const assign = (label: string, value: string) =>
  // oxlint-disable-next-line no-eval -- Execute the fixed local probe under jsdom.
  window.eval(
    template.replaceAll("LABEL", JSON.stringify(label)).replaceAll("VALUE", JSON.stringify(value)),
  );

afterEach(() => {
  document.body.innerHTML = "";
});

describe("native trial probe exact field assignment", () => {
  it.each(["input", "textarea"])("fills a wrapping %s label", (tag) => {
    document.body.innerHTML = `<label>标题<${tag}></${tag}></label>`;
    expect(assign("标题", "Synthetic")).toBe(true);
    expect(document.querySelector<HTMLInputElement | HTMLTextAreaElement>(tag)?.value).toBe(
      "Synthetic",
    );
  });
  it("excludes select options from label text and selects exact value", () => {
    document.body.innerHTML =
      '<label>驱动<select><option value="other">Other</option><option value="scopecat.virtual.vna">Virtual network analyzer</option></select></label>';
    expect(assign("驱动", "scopecat.virtual.vna")).toBe(true);
    expect(document.querySelector("select")?.value).toBe("scopecat.virtual.vna");
  });
  it("supports explicit for/control labels", () => {
    document.body.innerHTML = '<label for="title">标题</label><input id="title">';
    expect(assign("标题", "Synthetic")).toBe(true);
  });
  it("does not fill missing or disabled fields", () => {
    document.body.innerHTML = "<label>标题<input disabled></label>";
    expect(assign("不存在", "x")).toBe(false);
    expect(assign("标题", "x")).toBe(false);
    expect(document.querySelector("input")?.value).toBe("");
  });
  it("rejects duplicate labels without modifying either input", () => {
    document.body.innerHTML = "<label>标题<input></label><label>标题<input></label>";
    expect(() => assign("标题", "x")).toThrow("Ambiguous probe label");
    expect([...document.querySelectorAll("input")].every((e) => e.value === "")).toBe(true);
  });
  it("rejects missing select option", () => {
    document.body.innerHTML =
      '<label>驱动<select><option value="other">Other</option></select></label>';
    expect(() => assign("驱动", "missing")).toThrow("Missing exact probe select option");
    expect(document.querySelector("select")?.value).toBe("other");
  });
});

const diagnosticTemplate = source.match(/PAGE_DIAGNOSTICS = r"""([\s\S]*?)"""/)?.[1];
if (!diagnosticTemplate) throw new Error("Packaged probe diagnostics not found");
const diagnose = (
  origin: string,
): { error?: string; visibleText?: string; labels?: { text: string }[] } =>
  // oxlint-disable-next-line no-eval -- Execute the fixed local probe under jsdom.
  window.eval(diagnosticTemplate.replaceAll("EXPECTED_ORIGIN", JSON.stringify(origin)));

it("diagnostics redact form and editable subtree text", () => {
  document.body.innerHTML = `<h1>Owned page</h1><label>Title<textarea>private initial text</textarea></label>
    <label>Driver<select><option>private option</option></select></label>
    <input value="private value"><div contenteditable>private edit</div><p hidden>private hidden</p>`;
  const data = diagnose(location.origin);
  expect(data.visibleText).toContain("Owned page");
  expect(JSON.stringify(data)).not.toContain("private");
  expect(data.labels?.map((label) => label.text)).toEqual(["Title", "Driver"]);
});

it("diagnostics check origin inside the evaluated script before reading DOM", () => {
  document.body.innerHTML = "Private destination";
  expect(diagnose("https://different.invalid")).toEqual({
    error: "Diagnostic page is not the owned daemon origin",
  });
});

it("rejects a label with multiple controls without choosing the first", () => {
  document.body.innerHTML = "<label>标题<input><textarea></textarea></label>";
  expect(() => assign("标题", "x")).toThrow("Ambiguous probe control");
  expect(document.querySelector("input")?.value).toBe("");
});

it("diagnostics omit labels hidden on themselves", () => {
  document.body.innerHTML =
    '<label hidden>hidden-secret<input></label><label aria-hidden="true">aria-secret<input></label><label style="display:none">css-secret<input></label><label>Visible<input></label>';
  const data = diagnose(location.origin);
  expect(data.labels?.map((label) => label.text)).toEqual(["Visible"]);
  expect(JSON.stringify(data)).not.toContain("secret");
});

it("diagnostics omit labels under hidden ancestors", () => {
  document.body.innerHTML =
    '<section hidden><label>ancestor-secret<input></label></section><section aria-hidden="true"><label>aria-secret<input></label></section><section style="visibility:hidden"><label>css-secret<input></label></section><label>Visible<input></label>';
  const data = diagnose(location.origin);
  expect(data.labels?.map((label) => label.text)).toEqual(["Visible"]);
  expect(JSON.stringify(data)).not.toContain("secret");
});

// Execute only the actual Python navigation generator definitions, without
// importing application modules or invoking a native runtime.
const navigationPrograms: Record<string, string[]> = JSON.parse(
  execFileSync(
    process.platform === "win32" ? "python" : "python3",
    [
      "-c",
      `
import ast, json, sys
source = open(sys.argv[1], encoding="utf-8").read()
names = {"navigation_script", "navigate_workspace"}
nodes = [node for node in ast.parse(source).body if
    isinstance(node, ast.FunctionDef) and node.name in names or
    isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "NAVIGATION_ACTION" for t in node.targets)]
module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])
namespace = {"json":json, "wait":lambda check, *args: check()}
exec(compile(ast.fix_missing_locations(module), "actual-navigation-generator", "exec"), namespace)
result = {}
for label in ["设置 / Settings", "设备与驱动 / Devices and drivers"]:
    scripts = []
    def evaluate(script):
        scripts.append(script)
        return True
    namespace["navigate_workspace"](evaluate, label)
    result[label] = scripts
print(json.dumps(result))
`,
      resolve("../../packages/scopecat-app/src/scopecat_app/trial_window_probe.py"),
    ],
    { encoding: "utf8" },
  ),
);
const navigate = (label: string, action: string): boolean => {
  const index = ["ready", "click", "selected"].indexOf(action);
  // oxlint-disable-next-line no-eval -- Execute actual Python-generated probe JS in jsdom.
  return window.eval(navigationPrograms[label]![index]!);
};
// Reproduce the old Python ensure_ascii CSS-escaping error for the regression.
const pythonJSON = (value: string) =>
  JSON.stringify(value).replace(
    /[\u0080-\uFFFF]/g,
    (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`,
  );

it("actual Chinese navigation expressions select exactly the real target", () => {
  document.body.innerHTML =
    '<nav><button aria-current="page">Current</button><button aria-label="设置 / Settings">设置</button><button aria-label="设备与驱动 / Devices and drivers">设备</button></nav>';
  for (const label of ["设置 / Settings", "设备与驱动 / Devices and drivers"]) {
    const target = [...document.querySelectorAll("button")].find(
      (e) => e.getAttribute("aria-label") === label,
    )!;
    target.addEventListener("click", () => {
      document.querySelectorAll("[aria-current]").forEach((e) => e.removeAttribute("aria-current"));
      target.setAttribute("aria-current", "page");
    });
    expect(navigate(label, "ready")).toBe(true);
    expect(navigate(label, "selected")).toBe(false);
    expect(navigate(label, "click")).toBe(true);
    expect(navigate(label, "selected")).toBe(true);
  }
});

it("actual navigation rejects missing current, disabled and duplicate target", () => {
  document.body.innerHTML = '<nav><button aria-label="设置 / Settings">设置</button></nav>';
  expect(navigate("设置 / Settings", "ready")).toBe(false);
  document.querySelector("button")!.setAttribute("aria-current", "page");
  document.querySelector("button")!.disabled = true;
  expect(navigate("设置 / Settings", "ready")).toBe(false);
  document.querySelector("button")!.disabled = false;
  document.querySelector("nav")!.append(document.querySelector("button")!.cloneNode(true));
  expect(navigate("设置 / Settings", "ready")).toBe(false);
  expect(() => navigate("设置 / Settings", "click")).toThrow("not uniquely ready");
});

it("reproduces the old double-encoded Unicode CSS failure", () => {
  document.body.innerHTML = '<nav><button aria-label="设置 / Settings">设置</button></nav>';
  const oldSelector = `button[aria-label=${pythonJSON("设置 / Settings")}]`;
  expect(document.querySelector(oldSelector)).toBeNull();
  expect(
    [...document.querySelectorAll("button")].some(
      (e) => e.getAttribute("aria-label") === "设置 / Settings",
    ),
  ).toBe(true);
});

const controlPrograms: { button: string[]; recent: string[] } = JSON.parse(
  execFileSync(
    process.platform === "win32" ? "python" : "python3",
    [
      "-c",
      `
import ast, json, sys
source = open(sys.argv[1], encoding="utf-8").read()
names = {"button_script", "recent_result_script"}
constants = {"BUTTON_ACTION", "RECENT_RESULT_ACTION"}
nodes = [node for node in ast.parse(source).body if
    isinstance(node, ast.FunctionDef) and node.name in names or
    isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in node.targets)]
module = ast.Module(body=nodes, type_ignores=[])
namespace = {"json":json}
exec(compile(ast.fix_missing_locations(module), "actual-control-generators", "exec"), namespace)
print(json.dumps({
    "button": [namespace["button_script"]("查看已保存结果", action) for action in ["ready", "click"]],
    "recent": [namespace["recent_result_script"]("receipt-id", action) for action in ["ready", "click", "selected"]],
}))
`,
      resolve("../../packages/scopecat-app/src/scopecat_app/trial_window_probe.py"),
    ],
    { encoding: "utf8" },
  ),
);
const control = (kind: "button" | "recent", action: "ready" | "click" | "selected"): boolean => {
  // oxlint-disable-next-line no-eval -- Exercise actual Python-generated probe selectors in jsdom.
  return window.eval(controlPrograms[kind][["ready", "click", "selected"].indexOf(action)]!);
};

describe("native probe frontend-button actions", () => {
  it("clicks the rendered Chinese result button's actual handler", () => {
    document.body.innerHTML = "<button>查看已保存结果</button>";
    const dispatched: string[] = [];
    document.querySelector("button")!.addEventListener("click", () => dispatched.push("result"));
    expect(control("button", "ready")).toBe(true);
    expect(dispatched).toEqual([]);
    expect(control("button", "click")).toBe(true);
    expect(dispatched).toEqual(["result"]);
  });

  it.each([
    "<button disabled>查看已保存结果</button>",
    "<fieldset disabled><button>查看已保存结果</button></fieldset>",
    "<section hidden><button>查看已保存结果</button></section>",
    '<section aria-hidden="true"><button>查看已保存结果</button></section>',
    '<section style="display:none"><button>查看已保存结果</button></section>',
    '<section style="visibility:hidden"><button>查看已保存结果</button></section>',
    "<button>查看已保存结果</button><button>查看已保存结果</button>",
    '<a href="/result">查看已保存结果</a>',
  ])("refuses unavailable or ambiguous frontend targets: %s", (html) => {
    document.body.innerHTML = html;
    expect(control("button", "ready")).toBe(false);
    expect(() => control("button", "click")).toThrow("not uniquely ready");
  });

  it("does not confuse a hidden stale button with the current frontend button", () => {
    document.body.innerHTML =
      "<button hidden>查看已保存结果</button><button>查看已保存结果</button>";
    expect(control("button", "ready")).toBe(true);
  });

  it("selects the exact recent result and requires its pressed acknowledgment", () => {
    document.body.innerHTML =
      '<button data-testid="recent-result-other" aria-pressed="true">Other</button><button data-testid="recent-result-receipt-id" aria-pressed="false">Result</button>';
    const target = document.querySelector<HTMLButtonElement>(
      '[data-testid="recent-result-receipt-id"]',
    )!;
    target.addEventListener("click", () => target.setAttribute("aria-pressed", "true"));
    expect(control("recent", "ready")).toBe(true);
    expect(control("recent", "selected")).toBe(false);
    expect(control("recent", "click")).toBe(true);
    expect(control("recent", "selected")).toBe(true);
  });

  it.each([
    '<button data-testid="recent-result-other">Wrong result</button>',
    '<fieldset disabled><button data-testid="recent-result-receipt-id">Result</button></fieldset>',
    '<button data-testid="recent-result-receipt-id">Result</button><button data-testid="recent-result-receipt-id">Duplicate</button>',
  ])("rejects a missing, disabled or ambiguous recent result: %s", (html) => {
    document.body.innerHTML = html;
    expect(control("recent", "ready")).toBe(false);
    expect(() => control("recent", "click")).toThrow("not uniquely ready");
  });
});
