// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, expect, it, vi } from "vitest";

const script = readFileSync(
  resolve(process.cwd(), "../../packages/lab-tools/src/lab_tools/desktop_page.js"),
  "utf8",
);

afterEach(() => {
  document.body.innerHTML = "";
});

function recovery(reset: (skip: boolean) => Promise<boolean>) {
  document.body.innerHTML =
    '<input id="skip-backup" type="checkbox"><button>Reset</button><p id="progress"></p><p id="error"></p>';
  // Execute the shipped page JS, with only the native bridge replaced.
  // eslint-disable-next-line @typescript-eslint/no-implied-eval -- run the shipped native page script
  const resetData = new Function("document", "window", "pywebview", `${script}\nreturn resetData;`)(
    document,
    { addEventListener() {} },
    { api: { reset_data: reset } },
  ) as () => Promise<void>;
  const checkbox = document.querySelector<HTMLInputElement>("input")!;
  document.querySelector("button")!.addEventListener("click", () => {
    void resetData();
  });
  return { checkbox, resetData };
}

it.each(["cancel", "failure"])(
  "consumes skip once after %s, then defaults to backup",
  async (outcome) => {
    const reset = vi.fn(async (_skip: boolean) => {
      if (outcome === "failure") throw new Error("backup failed");
      return false;
    });
    const { checkbox, resetData } = recovery(reset);
    checkbox.click();
    document.querySelector("button")!.click();
    expect(checkbox.checked).toBe(false);
    await vi.waitFor(() => expect(document.querySelector("button")!.disabled).toBe(false));
    await resetData();
    expect(reset.mock.calls.map((args) => args[0])).toEqual([true, false]);
  },
);

it("does not consume a new selection when pending rejects another operation", async () => {
  let finish!: (changed: boolean) => void;
  const reset = vi.fn(
    (_skip: boolean) =>
      new Promise<boolean>((done) => {
        finish = done;
      }),
  );
  const { checkbox, resetData } = recovery(reset);
  checkbox.click();
  const first = resetData();
  expect(checkbox.checked).toBe(false);
  checkbox.click();
  await resetData();
  expect(checkbox.checked).toBe(true);
  expect(reset).toHaveBeenCalledTimes(1);
  finish(false);
  await first;
  const next = resetData();
  expect(checkbox.checked).toBe(false);
  expect(reset.mock.calls.map((args) => args[0])).toEqual([true, true]);
  finish(false);
  await next;
});
