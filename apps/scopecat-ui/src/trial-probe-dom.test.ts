// @vitest-environment jsdom
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
