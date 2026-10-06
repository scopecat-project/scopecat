import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test } from "@playwright/test";

const root = resolve(process.cwd(), "../..");
function uv(args: string[]) {
  const env = { ...process.env };
  delete env.SCOPECAT_DAEMON_URL;
  const result = spawnSync("uv", ["run", "--locked", "--project", root, ...args], {
    encoding: "utf8",
    env,
    timeout: 30_000,
  });
  if (result.error || result.status !== 0)
    throw new Error(result.error?.message ?? result.stdout + result.stderr);
}
async function start(project: string) {
  uv(["scopecat", "start", project, "--port", "0", "--static-dir", resolve("dist")]);
  return (
    JSON.parse(await readFile(join(project, ".scopecat/daemon.json"), "utf8")) as {
      base_url: string;
    }
  ).base_url;
}

test("share without runs, inspect/cancel, derive in an empty receiver and edit normally", async ({
  page,
}) => {
  test.setTimeout(120_000);
  page.setDefaultTimeout(10_000);
  const sender = await mkdtemp(join(tmpdir(), "scopecat-exchange-sender-"));
  const receiver = await mkdtemp(join(tmpdir(), "scopecat-exchange-receiver-"));
  const started: string[] = [];
  try {
    uv(["scopecat", "init", sender]);
    const senderUrl = await start(sender);
    started.push(sender);
    // Author saves initial inputs through the ordinary public SDK; no acquisition.
    uv([
      "python",
      "-c",
      `
import sys
from pathlib import Path
import scopecat as sc
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "src"))
from scopecat_lab.authored.parameters import initial_parameters
content = initial_parameters()
with sc.open_project(root).connect() as lab:
    lab.parameters.save(name="starter-initial", catalog=content.parameter_catalog, parameters=content.parameter_snapshot)
`,
      sender,
    ]);
    // Recipient has no bootstrap, devices, parameter catalog or source registration.
    await writeFile(join(receiver, "scopecat.toml"), "[lab]\n");
    const receiverUrl = await start(receiver);
    started.push(receiver);
    await page.goto(`${senderUrl}/#config`);
    await page.getByRole("button", { name: "Configuration", exact: true }).click();
    await page.getByRole("button", { name: "Export configuration…", exact: true }).click();
    await page
      .getByRole("combobox", { name: "Parameter version", exact: true })
      .selectOption("starter-initial");
    await page.getByLabel("File label", { exact: true }).fill("Colleague inputs");
    await page.getByRole("button", { name: "Review export", exact: true }).click();
    const downloading = page.waitForEvent("download");
    await page.getByRole("button", { name: "Save configuration file", exact: true }).click();
    const file = await (await downloading).path();
    expect(file).toBeTruthy();
    await page.goto(receiverUrl);
    await page.getByRole("button", { name: "Configuration", exact: true }).click();
    const upload = page.getByLabel("Import configuration file");
    await upload.setInputFiles(file);
    await expect(page.getByText("Initial values included", { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "Cancel inspection", exact: true }).click();
    expect(
      await (await page.request.get(`${receiverUrl}/api/v1/configuration-exchange/imports`)).json(),
    ).toEqual([]);
    expect(
      (await (await page.request.get(`${receiverUrl}/api/v1/parameters/revisions`)).json()).items,
    ).toEqual([]);
    await upload.setInputFiles(file);
    await page.getByLabel("Name for my new parameter branch").fill("My inputs");
    await page.getByRole("button", { name: "Create my copy", exact: true }).click();
    await expect(page.getByText(/Created parameter branch “My inputs”/)).toBeVisible();
    await expect(
      page.getByRole("combobox", { name: "Saved parameter version", exact: true }),
    ).toHaveValue("My inputs");
    await page.getByRole("button", { name: "Edit a copy", exact: true }).click();
    await page.getByRole("textbox", { name: "response[1].scale", exact: true }).fill("2");
    await page.getByText("Save a parameter checkpoint", { exact: true }).click();
    await page.getByLabel("New version name", { exact: true }).fill("My adjusted inputs");
    await page.getByRole("button", { name: "Save parameter version", exact: true }).click();
    await expect(
      page.getByRole("combobox", { name: "Saved parameter version", exact: true }),
    ).toHaveValue("My adjusted inputs");
    await page.reload();
    await page.getByText("Previously imported originals (latest 100)", { exact: true }).click();
    await page.getByRole("button", { name: "Colleague inputs", exact: true }).click();
    await page.getByLabel("Name for my new parameter branch").fill("My inputs");
    await page.getByRole("button", { name: "Create my copy", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("already uses this name");
    await page.getByLabel("Name for my new parameter branch").fill("My second copy");
    await page.getByRole("button", { name: "Create my copy", exact: true }).click();
    await expect(page.getByText(/Created parameter branch “My second copy”/)).toBeVisible();
    const originals = await (
      await page.request.get(`${receiverUrl}/api/v1/configuration-exchange/imports`)
    ).json();
    expect(originals).toHaveLength(1);
    expect(originals[0].derivations).toHaveLength(2);
  } finally {
    for (const project of started.toReversed()) uv(["scopecat", "stop", project]);
    await Promise.all(
      [sender, receiver].map((project) => rm(project, { recursive: true, force: true })),
    );
  }
});
