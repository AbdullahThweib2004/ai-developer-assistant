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
  await expect(page.getByTestId("index-button")).toHaveText(/Rebuild index/);
  await expect(page.getByText("Index ready")).toBeHidden({ timeout: 10_000 }); // toast gone
  // History persists on the demo server across tests: start from an empty chat.
  await page.getByTestId("new-chat").click();
  await expect(page.getByTestId("empty-state")).toBeVisible();
}

/** Client-side navigation through the sidebar (keeps this session's state). */
export async function go(page: Page, label: string) {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: label, exact: true }).click();
}

export async function ask401(page: Page) {
  await page.getByTestId("example-question").filter({ hasText: QUESTION }).click();
  await expect(page.getByTestId("answer")).toBeVisible({ timeout: 30_000 });
}
