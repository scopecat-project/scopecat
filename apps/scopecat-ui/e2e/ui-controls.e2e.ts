import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";

const ROOT = resolve(process.cwd(), "../..");
function uv(args: string[]) {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", ROOT, ...args], {
    env,
    encoding: "utf8",
    timeout: 60_000,
  });
  if (result.error || result.status !== 0)
    throw new Error(result.error?.message ?? result.stdout + result.stderr);
}

test("shared controls remain readable, keyboard accessible and contained across pages", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-controls-"));
  try {
    for (const name of ["src", "scopecat.toml"])
      await cp(join(ROOT, "testing/fixtures/retained-signal", name), join(project, name), {
        recursive: true,
      });
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
    const { base_url: baseUrl } = JSON.parse(
      await readFile(join(project, ".scopecat/daemon.json"), "utf8"),
    );
    // Exercise desktop-only fields without invoking any native operation.
    await page.addInitScript(() => {
      Object.assign(window, {
        pywebview: {
          api: {
            status: async () => ({
              home: "/test/application",
              sources: [],
              installation: { python: "/test/python", environment: {} },
            }),
            set_window_title: async () => {},
          },
        },
      });
    });
    for (const width of [1280, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      for (const [route, heading] of [
        ["history", "Data"],
        ["launch", "Experiments"],
        ["help", "Help"],
        ["settings", "Application settings"],
      ]) {
        await page.goto(`${baseUrl}/#${route}`);
        await expect(
          page.getByRole("heading", { name: heading, exact: true }).first(),
        ).toBeVisible();
        if (route === "settings") {
          const input = page.getByLabel("Author directory", { exact: true });
          await expect(input).toBeVisible();
          await input.focus();
          await page.keyboard.press("Tab");
          const next = page.getByLabel("Execution Python", { exact: true });
          await expect(next).toBeFocused();
          await expect(next).toHaveCSS("outline-width", "2px");
          await expect(
            page.getByRole("button", { name: "Add code folder", exact: true }),
          ).toBeDisabled();
          expect((await input.boundingBox())!.height).toBeGreaterThanOrEqual(36);
        }
        if (route === "history") {
          const select = page.getByLabel("Research project", { exact: true });
          await select.focus();
          await page.keyboard.press("Tab");
          await expect(page.getByLabel("Sample", { exact: true })).toBeFocused();
        }
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
          width,
        );
        await testInfo.attach(`${route}-${width}`, {
          body: await page.screenshot({ fullPage: true }),
          contentType: "image/png",
        });
      }
    }
    for (const colorScheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme });
      // The application currently supports a dark theme under both OS preferences.
      await expect(page.locator("html")).toHaveCSS("color-scheme", "dark");
    }
  } finally {
    uv(["python", "-m", "scopecat_server.cli", "stop", project]);
    await rm(project, { recursive: true, force: true });
  }
});
