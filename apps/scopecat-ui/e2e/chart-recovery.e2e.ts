import { test, expect } from "@playwright/test";
import { mkdtemp, readFile, readdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { startDeviceFixture } from "./device-fixture";
import type { components } from "../src/api-schema";

type Pending = components["schemas"]["PendingDirectMeasurement"];
const identity = (pending: Pending) => {
  const { session_id, instrument_id, command_id } = pending.selection;
  return { session_id, instrument_id, command_id };
};
const digest = (bytes: Buffer) => createHash("sha256").update(bytes).digest("hex");

for (const failure of [false, true]) {
  test(`chart ${failure ? "import rejection" : "delayed load"} preserves owner and full original`, async ({
    page,
  }, testInfo) => {
    test.setTimeout(90_000);
    const root = await mkdtemp(join(tmpdir(), "scopecat-chart-recovery-"));
    const fixture = startDeviceFixture(
      "../../packages/scopecat-server/tests/fixtures/device_curve_daemon.py",
      root,
    );
    let vite: ReturnType<typeof spawn> | undefined;
    let viteLog = "";
    const events: object[] = [];
    const add = (event: object) => events.push({ time: new Date().toISOString(), ...event });
    let pending: Pending | undefined;
    let backend = "";
    let saved = false;
    let collections = 0;
    let failed = false;
    const cleanupErrors: unknown[] = [];
    page.on("request", (r) => {
      if (r.method() === "POST" && r.url().endsWith("/collect-review")) collections++;
    });
    page.on("response", (r) => {
      if (
        /EChartRuntime|echarts_|collect-review|direct-measurements|instrument-sessions/.test(
          r.url(),
        )
      )
        add({
          url: r.url(),
          method: r.request().method(),
          status: r.status(),
          statusText: r.statusText(),
        });
    });
    page.on("console", (message) => add({ console: message.type(), message: message.text() }));
    page.on("pageerror", (error) => add({ pageerror: error.message }));
    page.on("websocket", (socket) => {
      add({ websocket: socket.url() });
      socket.on("framereceived", (event) => add({ hmr: String(event.payload) }));
    });
    try {
      await expect
        .poll(async () => {
          try {
            return await readFile(join(root, "url"), "utf8");
          } catch {
            return "";
          }
        })
        .not.toBe("");
      backend = await readFile(join(root, "url"), "utf8");
      await writeFile(join(root, "endpoint.json"), JSON.stringify({ url: backend }));
      const script = `import {createServer} from 'vite'; import {writeFile} from 'node:fs/promises'; const s=await createServer({cacheDir:${JSON.stringify(join(root, "vite-cache"))},server:{host:'127.0.0.1',port:0,strictPort:true}}); await s.listen(); await writeFile(${JSON.stringify(join(root, "vite-url"))},s.resolvedUrls.local[0]); process.stdin.on('data',async()=>{await s.close();process.exit(0)});`;
      vite = spawn("node", ["--input-type=module", "-e", script], {
        env: {
          ...process.env,
          DEBUG: "vite:deps,vite:hmr",
          SCOPECAT_DEV_ENDPOINT_FILE: join(root, "endpoint.json"),
        },
        stdio: ["pipe", "pipe", "pipe"],
      });
      vite.stdout?.on("data", (chunk) => {
        viteLog += String(chunk);
      });
      vite.stderr?.on("data", (chunk) => {
        viteLog += String(chunk);
      });
      await expect
        .poll(async () => {
          try {
            return await readFile(join(root, "vite-url"), "utf8");
          } catch {
            return "";
          }
        })
        .not.toBe("");
      const url = (await readFile(join(root, "vite-url"), "utf8")).replace(/\/$/, "");
      await page.goto(url + "/#settings");
      await page.getByLabel("操作者名称", { exact: true }).fill("chart-recovery-test");
      await page.getByRole("button", { name: "保存操作者", exact: true }).click();
      await expect(page.getByText("操作者已保存", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: /Devices and drivers$/, exact: true }).click();
      await page.getByRole("button", { name: "添加设备", exact: true }).click();
      const editor = page.getByRole("dialog");
      await editor.getByLabel("设备名称").fill("Chart recovery virtual VNA");
      await editor.getByRole("button", { name: "保存设备", exact: true }).click();
      await expect(editor).toBeHidden();
      const devices = await (await page.request.get(backend + "/api/v1/devices")).json();
      await page.goto(url + "/?device-control=" + encodeURIComponent(devices.items[0].device.id));
      await page.getByRole("button", { name: /^连接(?:并读取)?$/, exact: true }).click();
      // Do not reload the document: its in-memory session is the legitimate owner.
      let attempts = 0;
      await page.route("**/src/ui/EChartRuntime.tsx*", async (route) => {
        attempts++;
        add({
          importAttempt: attempts,
          url: route.request().url(),
          injected: failure && attempts === 1,
        });
        if (failure && attempts === 1)
          return route.fulfill({
            status: 503,
            contentType: "text/javascript",
            body: "// one bounded module-load failure",
          });
        if (!failure && attempts === 1) await new Promise((done) => setTimeout(done, 250));
        return route.continue();
      });
      const collected = page.waitForResponse(
        (r) => r.url().endsWith("/collect-review") && r.request().method() === "POST",
      );
      await page.getByTitle("scopecat.network_sweep/v1 / ", { exact: true }).click();
      const receipt: components["schemas"]["CollectReviewReceipt"] = await (await collected).json();
      expect(receipt.status).toBe("collected");
      pending = receipt.pending!;
      await writeFile(join(root, "collection.json"), JSON.stringify(receipt, null, 2));
      const originalResponse = await page.request.post(
        backend + "/api/v1/direct-measurements/pending/receipt",
        { data: identity(pending) },
      );
      expect(originalResponse.ok()).toBe(true);
      const original = await originalResponse.body();
      await writeFile(join(root, "original.bin"), original);
      const owner = await (
        await page.request.get(
          backend + `/api/v1/instrument-sessions/${pending.selection.session_id}/recovery`,
        )
      ).json();
      await expect(page.getByRole("button", { name: "释放控制", exact: true })).toBeVisible();
      const chart = page.getByRole("img", { name: "s_parameter 局部曲线" }).locator("svg");
      if (failure) {
        await expect(page.getByRole("alert")).toContainText("图表暂时无法显示");
        await expect(page.getByRole("alert")).toContainText("请勿刷新正在控制设备的窗口");
        await expect(page.getByRole("button", { name: "保存所选结果", exact: true })).toBeEnabled();
        await expect(
          page.getByRole("button", { name: "Acquire sweep", exact: true }),
        ).toBeEnabled();
        await page.getByRole("button", { name: "保存所选结果", exact: true }).click();
        const save = page.getByRole("dialog", { name: "保存测量", exact: true });
        await save
          .getByLabel("标题", { exact: true })
          .fill("retained original through chart failure");
        await save.getByRole("button", { name: "取消", exact: true }).click();
        await page.screenshot({ path: join(root, "failure.png") });
        await page.getByRole("button", { name: "重试图表", exact: true }).click();
      }
      await expect(chart).toBeVisible();
      expect(attempts).toBe(failure ? 2 : 1);
      expect(collections).toBe(1);
      await expect(page.getByRole("button", { name: "释放控制", exact: true })).toBeVisible();
      const ownerAfter = await (
        await page.request.get(
          backend + `/api/v1/instrument-sessions/${pending.selection.session_id}/recovery`,
        )
      ).json();
      expect(ownerAfter).toEqual(owner);
      const after = await (
        await page.request.post(backend + "/api/v1/direct-measurements/pending/receipt", {
          data: identity(pending),
        })
      ).body();
      expect(after.equals(original)).toBe(true);
      await page.getByRole("button", { name: "保存所选结果", exact: true }).click();
      const save = page.getByRole("dialog", { name: "保存测量", exact: true });
      if (failure)
        await expect(save.getByLabel("标题", { exact: true })).toHaveValue(
          "retained original through chart failure",
        );
      const savedResponse = page.waitForResponse(
        (r) =>
          new URL(r.url()).pathname === "/api/v1/direct-measurements" &&
          r.request().method() === "POST",
      );
      const savedPreview = page.waitForResponse(
        (r) =>
          /\/direct-measurements\/[^/]+\/preview$/.test(new URL(r.url()).pathname) &&
          !r.url().includes("/pending/"),
      );
      await save.getByRole("button", { name: "保存", exact: true }).click();
      const record: components["schemas"]["DirectMeasurement"] = await (await savedResponse).json();
      saved = true;
      // The UI preview and a raw read each reserve working capacity. Complete the
      // UI read before the extra verification read, rather than racing the budget.
      const previewResponse = await savedPreview;
      expect(previewResponse.ok()).toBe(true);
      await previewResponse.finished();
      await expect(chart).toBeVisible();
      expect(record.selection).toMatchObject({
        session_id: pending.selection.session_id,
        instrument_id: pending.selection.instrument_id,
        command_id: pending.selection.command_id,
      });
      expect(record.description).toEqual(pending.description);
      expect(record.intent).toEqual(pending.intent);
      await writeFile(join(root, "saved.json"), JSON.stringify(record, null, 2));
      const durableResponse = await page.request.get(
        backend + `/api/v1/direct-measurements/${record.id}/receipt`,
      );
      const durable = await durableResponse.body();
      add({
        durableStatus: durableResponse.status(),
        bytes: durable.length,
        originalBytes: original.length,
        error: durableResponse.ok() ? undefined : durable.toString(),
      });
      expect(durableResponse.ok()).toBe(true);
      expect(durable.equals(original)).toBe(true);
      expect(record.receipt_digest).toBe("sha256:" + digest(original));
      await writeFile(join(root, "saved.json"), JSON.stringify(record, null, 2));
      await page.screenshot({ path: join(root, "recovered.png") });
      // Open saved data separately, retaining the original controller document.
      const result = await page.context().newPage();
      await result.goto(url + "/?saved-measurement=" + record.id);
      await expect(
        result.getByRole("img", { name: "s_parameter 局部曲线" }).locator("svg"),
      ).toBeVisible();
      await result.close();
      await expect(page.getByRole("button", { name: "释放控制", exact: true })).toBeVisible();
      add({
        verified: true,
        daemonPid: fixture.child.pid,
        selection: pending.selection,
        digest: digest(original),
        bytes: original.length,
        collections,
        attempts,
      });
    } catch (error) {
      failed = true;
      throw error;
    } finally {
      // Preserve the original before stopping even when a later assertion fails.
      if (pending && !saved) {
        try {
          const response = await page.request.post(backend + "/api/v1/direct-measurements", {
            data: pending.selection,
          });
          add({ teardownSaveStatus: response.status(), teardownSave: await response.text() });
        } catch (error) {
          add({ teardownSaveFailure: String(error) });
        }
      }
      try {
        await writeFile(join(root, "events.json"), JSON.stringify(events, null, 2));
        await writeFile(join(root, "vite.log"), viteLog);
        for (const name of await readdir(root)) {
          if (/\.(json|log|png|bin)$/.test(name))
            await testInfo.attach(name, { path: join(root, name) });
        }
        await testInfo.attach("retained-chart-evidence", {
          body: JSON.stringify({ root, daemonPid: fixture.child.pid }),
          contentType: "application/json",
        });
      } catch (error) {
        cleanupErrors.push(error);
        console.error("Evidence write failed", error);
      } finally {
        try {
          if (vite && vite.exitCode === null) {
            const child = vite;
            await new Promise<void>((done, reject) => {
              let forceTimer: ReturnType<typeof setTimeout> | undefined;
              const timer = setTimeout(() => {
                child.kill("SIGTERM");
                forceTimer = setTimeout(
                  () => reject(new Error("Owned Vite did not exit after SIGTERM")),
                  3000,
                );
              }, 10_000);
              child.once("exit", (code, signal) => {
                clearTimeout(timer);
                if (forceTimer !== undefined) clearTimeout(forceTimer);
                if (forceTimer !== undefined || code !== 0 || signal !== null)
                  reject(
                    new Error(
                      `Owned Vite required forced cleanup or exited abnormally: ${code}/${signal}`,
                    ),
                  );
                else done();
              });
              child.stdin?.end("stop\n");
            });
          }
        } catch (error) {
          cleanupErrors.push(error);
          console.error("Owned Vite cleanup failed", error);
        } finally {
          try {
            await fixture.stop();
          } catch (error) {
            cleanupErrors.push(error);
            console.error("Owned fixture cleanup failed", error);
          }
        }
      }
    }
    if (!failed && cleanupErrors.length)
      throw new AggregateError(cleanupErrors, "Chart fixture cleanup failed");
  });
}
