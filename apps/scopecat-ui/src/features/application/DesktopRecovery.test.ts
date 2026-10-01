import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { expect, it, vi } from "vitest";

const script = readFileSync(
  new URL("../../../../../packages/lab-tools/src/lab_tools/desktop_page.js", import.meta.url),
  "utf8",
);

function recovery(api: Record<string, unknown>) {
  const elements = {
    progress: { textContent: "", hidden: false },
    error: { textContent: "", hidden: false },
    "quit-options": { textContent: "", hidden: true },
  };
  const button = { disabled: false };
  const context = {
    window: {},
    pywebview: { api },
    document: {
      getElementById: (id: keyof typeof elements) => elements[id],
      querySelectorAll: () => [button],
    },
  };
  return {
    elements,
    button,
    context,
    invoke: (action: string) => runInNewContext(script + action, context),
  };
}

it("shows retry progress, suppresses duplicate clicks and restores controls on failure", async () => {
  let fail!: (error: Error) => void;
  const retry = vi.fn().mockReturnValue(
    new Promise<void>((_resolve, reject) => {
      fail = reject;
    }),
  );
  const page = recovery({ retry });
  const operation = page.invoke("retry(); retry();");
  expect(page.elements.progress.textContent).toContain("正在重新准备");
  expect(page.button.disabled).toBe(true);
  expect(retry).toHaveBeenCalledTimes(1);
  fail(new Error("Startup unavailable"));
  await operation;
  // The first request's rejection handler runs in the same microtask turn.
  expect(page.elements.error.textContent).toBe("Startup unavailable");
  expect(page.button.disabled).toBe(false);
});

it("keeps explicit stop available if the activity check fails", async () => {
  const page = recovery({ request_exit: vi.fn().mockRejectedValue(new Error("Offline")) });
  await page.invoke("quit();");
  expect(page.elements["quit-options"].hidden).toBe(false);
  expect(page.elements.error.textContent).toBe("Offline");
  expect(page.button.disabled).toBe(false);
});
