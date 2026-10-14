import { expect, type Page } from "@playwright/test";

/** Inspect the visible identity through the same disclosure a user opens. */
export async function expectRunIdentity(page: Page, runId: string) {
  await expect(page).toHaveURL((url) => url.searchParams.get("run") === runId);
  const header = page.getByTestId("run-detail-header");
  const details = header.locator("details").filter({
    has: page.getByText("Run details", { exact: true }),
  });
  if ((await details.getAttribute("open")) === null) {
    await details.getByText("Run details", { exact: true }).click();
  }
  await expect(details.getByTitle(runId, { exact: true })).toBeVisible();
  await expect(details.getByTitle(runId, { exact: true })).toHaveText(runId);
}
