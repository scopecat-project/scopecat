import { cp } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect, type Page } from "@playwright/test";

export async function copyAuthorWorkspace(project: string) {
  await cp(fileURLToPath(new URL("./fixtures/author-workspace", import.meta.url)), project, {
    recursive: true,
  });
}

export function prepareAuthorContexts(uv: (args: string[]) => unknown, project: string) {
  uv([
    "python",
    "-c",
    `
import sys
from pathlib import Path
import scopecat as sc
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "src"))
from ui_author.application import save_inputs
with sc.open_project(root).connect() as lab:
    save_inputs(lab)
`,
    project,
  ]);
}

export async function chooseAuthorContext(page: Page, setup = "browser-bench-a") {
  await page.getByRole("button", { name: "Choose parameter branch", exact: true }).click();
  await expect(page.getByRole("option", { name: /browser.*generation 1/ })).toBeAttached();
  await page.getByLabel("Parameter branch", { exact: true }).selectOption("browser");
  await page.getByRole("button", { name: "Use this parameter version", exact: true }).click();
  await page.getByLabel("Experiment setup", { exact: true }).selectOption(setup);
}

export async function reviewRetainedExperiment(page: Page) {
  const review = page.getByRole("region", { name: "Review recovered experiment", exact: true });
  await expect(review).toBeVisible();
  await expect(page.getByRole("button", { name: "Preview", exact: true })).toBeDisabled();
  await review.getByRole("button", { name: "Confirm reviewed input", exact: true }).click();
  await expect(review).toBeHidden();
}
