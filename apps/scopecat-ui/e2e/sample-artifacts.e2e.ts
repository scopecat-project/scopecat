import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";

const ROOT = resolve(process.cwd(), "../..");
function uv(args: string[]) {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", ROOT, ...args], {
    encoding: "utf8",
    env,
    timeout: 30_000,
  });
  if (result.error || result.status !== 0)
    throw new Error(result.error?.message ?? result.stdout + result.stderr);
}
test("opens a delivered sample diagram and explains undelivered references", async ({
  page,
}, testInfo) => {
  const project = await mkdtemp(join(tmpdir(), "scopecat-sample-attachment-"));
  let failed = false;
  try {
    await writeFile(join(project, "scopecat.toml"), "[lab]\n");
    uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
    const endpoint = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    };
    const bytes = await readFile(join(ROOT, "fixtures/core/sample_artifacts/diagram.png"));
    const imported = await page.request.post(`${endpoint.base_url}/api/v1/sample-artifacts`, {
      params: {
        artifact_id: "diagram",
        title: "Synthetic connection diagram",
        media_type: "image/png",
      },
      data: bytes,
      headers: { "Content-Type": "application/octet-stream" },
    });
    expect(imported.status()).toBe(201);
    const artifact: unknown = await imported.json();
    const created = await page.request.post(`${endpoint.base_url}/api/v1/samples`, {
      data: {
        operation_id: "synthetic-sample",
        sample_id: "synthetic-chip",
        kind: "chip",
        actor: "browser",
        content: {
          display_name: "Synthetic attachment sample",
          artifacts: [
            artifact,
            {
              id: "local",
              title: "Undelivered local document",
              uri: "file:///outside/manual.pdf",
              media_type: "application/pdf",
            },
            {
              id: "missing",
              title: "Missing stored image",
              uri: `sha256:${"1".repeat(64)}`,
              media_type: "image/png",
            },
          ],
        },
      },
    });
    expect(created.status()).toBe(201);
    await page.goto(`${endpoint.base_url}/?sample=synthetic-chip#samples`);
    const attachments = page.getByLabel("Sample attachments");
    await expect(attachments.getByText("Synthetic connection diagram")).toBeVisible();
    await expect(attachments.getByText("Attachment unavailable")).toHaveCount(2);
    await expect(attachments.getByText(/Stored attachment bytes are missing/)).toBeVisible();
    await expect(attachments.getByText(/Relative paths and file\/project URIs/)).toBeVisible();
    await expect(attachments.getByRole("link")).toHaveCount(1);
    const popupPromise = page.waitForEvent("popup");
    await attachments.getByRole("link", { name: "Open attachment" }).click();
    const popup = await popupPromise;
    await popup.waitForLoadState("load");
    await expect(popup.locator("img")).toBeVisible();
    await expect(popup.locator("img")).toHaveJSProperty("naturalWidth", 160);
    const response = await page.request.get(popup.url());
    expect(await response.body()).toEqual(bytes);
    expect(response.headers()["x-content-type-options"]).toBe("nosniff");
    await popup.close();
    const shot = testInfo.outputPath("sample-attachment-delivery.png");
    await page.screenshot({ path: shot, fullPage: true });
    await testInfo.attach("Delivered diagram and explicit repairs", {
      path: shot,
      contentType: "image/png",
    });
  } catch (error) {
    failed = true;
    await testInfo.attach("Project path", { body: project, contentType: "text/plain" });
    await testInfo.attach("Daemon log", {
      body: await readFile(join(project, ".scopecat/daemon.log"), "utf8").catch(
        () => "No daemon log",
      ),
      contentType: "text/plain",
    });
    throw error;
  } finally {
    uv(["scopecat", "stop", project]);
    if (!failed) await rm(project, { recursive: true, force: true });
  }
});
