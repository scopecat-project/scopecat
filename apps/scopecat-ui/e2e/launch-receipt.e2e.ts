import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { chooseAuthorContext, copyAuthorWorkspace, prepareAuthorContexts } from "./author-context";

const ROOT = resolve(process.cwd(), "../..");
function uv(args: string[]): string {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", ROOT, ...args], {
    encoding: "utf8",
    env,
    timeout: 30_000,
  });
  if (result.error || result.status !== 0)
    throw new Error(result.error?.message ?? result.stdout + result.stderr);
  return result.stdout.trim();
}

test("reopens a lost launch receipt after context changes without a second submission", async ({
  page,
}, testInfo) => {
  let failed = false;
  const project = await mkdtemp(join(tmpdir(), "scopecat-draft-submission-"));
  let originalId = "";
  let submissions = 0;
  try {
    await copyAuthorWorkspace(project);
    uv([
      "python",
      "-m",
      "scopecat_server.cli",
      "start",
      project,
      "--port",
      "0",
      "--static-dir",
      resolve("dist"),
    ]);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    prepareAuthorContexts(uv, project);
    await page.goto(`${endpoint.base_url}/#launch`);
    await page.getByLabel("实验类型", { exact: true }).selectOption("signal");
    await chooseAuthorContext(page);
    await page.getByRole("button", { name: "预览", exact: true }).click();
    await expect(page.getByText("预览就绪", { exact: true })).toBeVisible();
    await page.route("**/api/v1/experiment-launcher/submit", async (route) => {
      submissions += 1;
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      originalId = ((await response.json()) as { procedure_id: string }).procedure_id;
      await route.abort("failed");
    });
    await page.getByRole("button", { name: "开始采集" }).click();
    await expect(page.getByRole("heading", { name: "原始提交待确认" })).toBeVisible();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByRole("region", { name: "原始提交" })).toBeFocused();
    await expect(page.getByRole("region", { name: "原始提交" })).toBeInViewport();
    await page
      .getByRole("navigation", { name: "项目页面" })
      .getByRole("button", { name: /Configuration$/, exact: true })
      .click();
    await page.route("**/api/v1/experiment-launcher", async (route) => {
      const response = await route.fetch();
      const catalog = (await response.json()) as {
        entries: Array<{ id: string; description: string }>;
      };
      const changed = {
        ...catalog,
        entries: catalog.entries.map((entry) =>
          entry.id === "signal"
            ? { ...entry, description: entry.description + " Updated description." }
            : entry,
        ),
      };
      await route.fulfill({ response, json: changed });
    });
    await page
      .getByRole("navigation", { name: "项目页面" })
      .getByRole("button", { name: /Experiments$/, exact: true })
      .click();
    await expect(page.getByRole("region", { name: "核实恢复的实验", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "预览", exact: true })).toBeDisabled();
    // Recovery reads the original receipt without reviewing or resubmitting the changed draft.
    await expect(page.getByRole("button", { name: "开始采集", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Retry original submission" })).toHaveCount(0);
    await page.getByRole("button", { name: "核实原始提交" }).click();
    await page.getByRole("button", { name: "查看已提交任务" }).click();
    await expect(page).toHaveURL(new RegExp(`procedure=${originalId}`));
    expect(submissions).toBe(1);
    const shot = testInfo.outputPath("original-launch-submission.png");
    await page.screenshot({ path: shot, fullPage: true });
    await testInfo.attach("Original admitted identity recovered read-only", {
      path: shot,
      contentType: "image/png",
    });
  } catch (error) {
    failed = true;
    await testInfo.attach("Isolated project path", { body: project, contentType: "text/plain" });
    const daemonLog = await readFile(join(project, ".scopecat/daemon.log"), "utf8").catch(
      () => "No daemon log was created.",
    );
    await testInfo.attach("Daemon log", { body: daemonLog, contentType: "text/plain" });
    throw error;
  } finally {
    uv(["python", "-m", "scopecat_server.cli", "stop", project]);
    // Preserve failed project state and logs for diagnosis.
    if (!failed) await rm(project, { recursive: true, force: true });
  }
});
