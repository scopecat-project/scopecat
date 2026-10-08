import { expect, type Page } from "@playwright/test";

export function prepareReferenceContexts(uv: (args: string[]) => unknown, project: string) {
  uv([
    "python",
    "-c",
    `
import sys
from pathlib import Path
import scopecat as sc
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "src"))
from reference_lab.configuration import initial_parameters, initial_setup
values = initial_parameters()
with sc.open_project(root).connect() as lab:
    revision = lab.parameters.save(name="browser-values", catalog=values.catalog, parameters=values.parameters)
    lab.parameters.create_branch("browser", revision=revision)
    resolved = lab.setup.import_recipe(initial_setup(), name="browser-template")
    setup = lab.setup.definition(resolved.resolution.definition_id).definition
    lab.setup.save(setup, name="browser-bench-a")
    assert setup.domain_target is not None
    alternate = setup.model_copy(update={"domain_target": setup.domain_target.model_copy(update={"id": "alternate-browser-bench"})})
    lab.setup.save(alternate, name="browser-bench-b")
    assert lab.config.registry().entries == ()
`,
    project,
  ]);
}

export async function chooseReferenceContext(page: Page, setup = "browser-bench-a") {
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
