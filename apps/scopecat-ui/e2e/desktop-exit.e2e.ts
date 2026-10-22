import { cp, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { ROOT, startProject, stopProject } from "./daemon";

test("exit wait retains future plans and Cancel recovers a lost start reply", async ({
  page,
  request,
}) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-exit-ui-"));
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
    const baseUrl = await startProject(project);
    const schedule = await request.post(`${baseUrl}/api/v1/procedure-schedules`, {
      data: {
        schedule_id: "future-retained",
        definition: { id: "exit-fixture", version: "1", fingerprint: `sha256:${"a".repeat(64)}` },
        intent: {},
        due_at: "2099-01-01T00:00:00Z",
      },
    });
    expect(schedule.ok()).toBeTruthy();
    const before = await schedule.json();
    await page.addInitScript(() => {
      let loseReply = true;
      Object.assign(globalThis, {
        pywebview: {
          api: {
            set_window_title: async () => {},
            request_exit: async () => (await fetch("/api/v1/application-activity")).json(),
            exit_status: async () => ({
              ...((await (await fetch("/api/v1/application-exit")).json()) as Record<
                string,
                unknown
              >),
              file_operations: 0,
              error: null,
            }),
            wait_for_idle: async (wait: boolean) => {
              const result = await fetch(`/api/v1/application-exit?wait=${wait}`, {
                method: "POST",
              });
              if (!result.ok) throw new Error(await result.text());
              if (wait && loseReply) {
                loseReply = false;
                throw new Error("Simulated lost start reply");
              }
            },
          },
        },
      });
    });
    await page.goto(baseUrl);
    await page.waitForFunction(
      () =>
        typeof (globalThis as unknown as { scopecatRequestExit?: () => void })
          .scopecatRequestExit === "function",
    );
    await page.evaluate(() =>
      (globalThis as unknown as { scopecatRequestExit?: () => void }).scopecatRequestExit?.(),
    );
    await page.getByRole("button", { name: "Quit when work finishes" }).click();
    await expect(page.getByRole("alert")).toHaveText("Simulated lost start reply");
    await expect(page.getByRole("region", { name: "Deferred and retained" })).toContainText(
      "future-retained",
    );
    expect((await (await request.get(`${baseUrl}/api/v1/application-exit`)).json()).draining).toBe(
      true,
    );
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect((await (await request.get(`${baseUrl}/api/v1/application-exit`)).json()).draining).toBe(
      false,
    );
    const retained = await request.get(
      `${baseUrl}/api/v1/procedure-schedules/by-id/future-retained`,
    );
    expect(await retained.json()).toEqual(before.schedule);
  } finally {
    stopProject(project);
    await rm(project, { recursive: true, force: true });
  }
});
