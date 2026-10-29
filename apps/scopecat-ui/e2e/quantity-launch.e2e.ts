import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";
import {
  chooseAuthorContext,
  copyAuthorWorkspace,
  prepareAuthorContexts,
  reviewRetainedExperiment,
} from "./author-context";

const ROOT = resolve(process.cwd(), "../..");
function uv(args: string[]): void {
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

test("strict Quantity survives saved draft reopen, preview and explicit acquisition", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const project = await mkdtemp(join(tmpdir(), "scopecat-quantity-e2e-"));
  let cleanupError: unknown;
  try {
    await copyAuthorWorkspace(project);
    const source = join(project, "src/ui_author/signal.py");
    await writeFile(
      source,
      (await readFile(source, "utf8")) +
        `

def voltage_value(amplitude: sc.Quantity) -> float:
    return amplitude.to("V").value

@sc.experiment
def voltage(experiment: sc.ExperimentContext, *,
    amplitude: Annotated[sc.Input[sc.Quantity], sc.QuantityType(dimension="voltage")] = sc.Quantity(2000, "mV"),
    duration: Annotated[sc.Input[sc.Quantity], sc.QuantityType(unit="ns", minimum=1000, maximum=2000)] = sc.Quantity(1, "us")
):
    return experiment.compute(fn=voltage_value, amplitude=amplitude)
`,
    );
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
    prepareAuthorContexts(uv, project);
    const { base_url } = JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8"));
    await page.goto(`${base_url}/#settings`);
    await page.getByLabel("操作者名称", { exact: true }).fill("Quantity tester");
    await page.getByRole("button", { name: "保存操作者", exact: true }).click();
    await expect(page.getByText("操作者已保存", { exact: true })).toBeVisible();
    await page.goto(`${base_url}/#launch`);
    await page.getByLabel("实验类型", { exact: true }).selectOption("voltage");
    // Selection also reads operator settings before committing the new form.
    // Do not click the previous experiment's context picker while that read is pending.
    await expect(
      page
        .getByRole("region", { name: "Selected experiment", exact: true })
        .getByRole("heading", { name: "voltage", exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel("amplitude value", { exact: true })).toBeVisible();
    await chooseAuthorContext(page);
    await expect(page.getByLabel("amplitude value", { exact: true })).toHaveValue("2000");
    await expect(page.getByLabel("amplitude unit", { exact: true })).toHaveValue("mV");
    await page.getByLabel("amplitude value", { exact: true }).fill("2500");
    await expect(page.getByText("实验输入已保存到应用数据。", { exact: true })).toBeVisible();
    const reopen = await page
      .getByRole("link", { name: "Reopen this saved draft" })
      .getAttribute("href");
    await page.goto(`${base_url}${reopen}`);
    await expect(page.getByLabel("amplitude value", { exact: true })).toHaveValue("2500");
    await expect(page.getByLabel("amplitude unit", { exact: true })).toHaveValue("mV");
    await reviewRetainedExperiment(page);
    const runs = async () =>
      (await (await page.request.get(`${base_url}/api/v1/runs`)).json()).items;
    expect(await runs()).toHaveLength(0);
    await page.getByLabel("amplitude unit", { exact: true }).fill("Hz");
    await page.getByRole("button", { name: "预览", exact: true }).click();
    await expect(page.getByText("请输入兼容的显式单位。", { exact: true })).toBeVisible();
    expect(await runs()).toHaveLength(0);
    await page.getByLabel("amplitude unit", { exact: true }).fill("mV");
    const check = async () => {
      const response = page.waitForResponse(
        (r) => r.url().endsWith("/experiment-launcher/preview") && r.request().method() === "POST",
      );
      await page.getByRole("button", { name: "预览", exact: true }).click();
      return response;
    };
    // Equivalent endpoints must pass, while the immediately adjacent values fail.
    expect((await check()).ok()).toBe(true);
    for (const value of ["0.9999999999999999", "2.0000000000000004"]) {
      await page.getByLabel("duration value", { exact: true }).fill(value);
      expect((await check()).ok()).toBe(false);
      expect(await runs()).toHaveLength(0);
    }
    await page.getByLabel("duration value", { exact: true }).fill("2");
    const preview = page.waitForResponse(
      (r) => r.url().endsWith("/experiment-launcher/preview") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "预览", exact: true }).click();
    const checked = await preview;
    expect(checked.status()).toBe(200);
    expect(checked.request().postDataJSON().inputs).toEqual({
      amplitude: { value: 2500, unit: "mV" },
      duration: { value: 2, unit: "us" },
    });
    expect(await runs()).toHaveLength(0);
    const submitted = page.waitForResponse(
      (r) => r.url().endsWith("/experiment-launcher/submit") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "开始采集", exact: true }).click();
    const admitted = await submitted;
    expect(admitted.status()).toBe(200);
    expect(admitted.request().postDataJSON().inputs).toEqual({
      amplitude: { value: 2500, unit: "mV" },
      duration: { value: 2, unit: "us" },
    });
    await expect(page.getByText("experiment: 已完成", { exact: true })).toBeVisible();
  } finally {
    try {
      uv(["python", "-m", "scopecat_server.cli", "stop", project]);
    } catch (error) {
      cleanupError = error;
    }
    await rm(project, { recursive: true, force: true });
  }
  if (cleanupError) throw cleanupError;
});
