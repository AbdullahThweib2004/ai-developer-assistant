import { expect, type Page } from "@playwright/test";

export const QUESTION = "Why do requests to /users/me return 401";

/** Open the app and make sure the demo project is indexed. */
export async function ready(page: Page, navigate = true) {
  if (navigate) await page.goto("/");
  const index = page.getByTestId("index-button");
  await expect(index).toBeVisible();
  if ((await index.textContent())?.includes("Index project")) {
    await index.click();
    await expect(page.getByText("Index ready")).toBeVisible({ timeout: 30_000 });
  }
  await expect(index).toHaveText(/Rebuild index/);
  await expect(page.getByText("Index ready")).toBeHidden({ timeout: 10_000 }); // toast gone
}
