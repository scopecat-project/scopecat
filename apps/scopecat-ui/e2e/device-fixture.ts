import { spawn } from "node:child_process";
import { resolve } from "node:path";
import { rm } from "node:fs/promises";
import type { TestInfo } from "@playwright/test";
import { uv } from "./daemon";

/** Spawn the actual Python process: terminating an uv wrapper leaves its child alive on Windows. */
export function startDeviceFixture(script: string, root: string) {
  const python = uv(["python", "-c", "import sys; print(sys.executable)"]);
  const env = { ...process.env };
  env.SCOPECAT_LOCATOR_HOME = resolve(root, "locators");
  delete env.SCOPECAT_DAEMON_URL;
  const child = spawn(python, [resolve(script), root, resolve("dist")], {
    env,
    stdio: ["pipe", "pipe", "pipe"],
  });
  let logs = "";
  let closed = false;
  child.stdout.resume();
  child.stdin.on("error", (error) => {
    logs += `Fixture stdin: ${error.message}\n`;
  });
  child.stderr.on("data", (chunk) => {
    logs += String(chunk);
  });
  const stopped = new Promise<void>((done, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => {
      closed = true;
      if (code === 0 && signal === null) done();
      else
        reject(new Error(`Fixture exited abnormally (code ${code}, signal ${signal}).\n${logs}`));
    });
  });
  // Install a rejection handler immediately, including when startup fails before polling starts.
  void stopped.catch(() => {});
  return {
    child,
    get logs() {
      return logs;
    },
    async stop() {
      if (closed) return stopped;
      child.stdin.end("stop\n");
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([
          stopped,
          new Promise<never>((_, reject) => {
            timer = setTimeout(
              () => reject(new Error(`Fixture did not stop normally.\n${logs}`)),
              15_000,
            );
          }),
        ]);
      } finally {
        if (timer !== undefined) clearTimeout(timer);
        // Only the owned software-fixture process is eligible for this cleanup fallback.
        if (!closed) child.kill("SIGTERM");
      }
    },
  };
}

/** Cleanup errors must not replace the browser assertion that caused teardown. */
export async function cleanDeviceFixture(
  fixture: ReturnType<typeof startDeviceFixture> | undefined,
  root: string,
  passed: boolean,
  testInfo: Pick<TestInfo, "attach">,
) {
  try {
    await fixture?.stop();
    await rm(root, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  } catch (error) {
    await testInfo.attach("fixture-cleanup", {
      body: `${String(error)}\n${fixture?.logs ?? ""}\nFixture root: ${root}`,
      contentType: "text/plain",
    });
    if (passed) throw error;
  }
}
