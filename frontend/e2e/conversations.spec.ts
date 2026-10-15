import { resolve } from "node:path";
import { expect, type Page, test } from "@playwright/test";
import { QUESTION, ready } from "./helpers";

// Demo mode: two projects (the demo app and its app/ package), each with its own conversations.
const PROJECT_B = resolve(process.cwd(), "../examples/fastapi-auth-demo/app");

async function switchProject(page: Page, nameOrPath: string) {
  await page.getByTestId("project-select").click();
  const option = page.getByRole("option", { name: new RegExp(`^${nameOrPath}`) });
  if (await option.count()) {
    await option.first().click();
  } else {
    await page.getByLabel("Project path").fill(nameOrPath);
    await page.getByLabel("Project path").press("Enter");
  }
}

async function askInComposer(page: Page, question: string) {
  await page.getByLabel("Message").fill(question);
  await page.getByLabel("Message").press("Enter");
  await expect(page.getByTestId("message").last().getByTestId("answer")).toBeVisible({ timeout: 30_000 });
}

test("conversations belong to one project, survive switching and reloads, and can be continued", async ({ page }) => {
  await ready(page);
  const scope = page.getByTestId("conversation-project");

  // Ask in project A.
  await page.getByTestId("example-question").filter({ hasText: QUESTION }).click();
  await expect(page.getByTestId("answer")).toBeVisible({ timeout: 30_000 });
  await expect(page).toHaveURL(/\/chat\/[\w-]+$/);
  const urlA = page.url();
  await expect(scope).toHaveText("fastapi-auth-demo");

  // Switch to project B: the chat is empty and scoped to B.
  await switchProject(page, PROJECT_B);
  await expect(scope).toHaveText("app");
  await expect(page.getByTestId("message")).toHaveCount(0);
  const index = page.getByTestId("index-button");
  if ((await index.textContent())?.includes("Index project")) {
    await index.click();
    await expect(page.getByText("Index ready")).toBeVisible({ timeout: 30_000 });
  }
  await expect(page.getByTestId("empty-state")).toBeVisible();
  await expect(page.getByTestId("conversation-item")).toHaveCount(0);

  // Ask in B.
  await askInComposer(page, "How does the auth module verify tokens?");
  await expect(page.getByTestId("message")).toHaveCount(1);
  await expect(page.getByTestId("message")).toContainText("How does the auth module verify tokens?");
  await expect(page.getByTestId("conversation-item")).toHaveCount(1);

  // Back to A: A's conversation is restored exactly.
  await switchProject(page, "fastapi-auth-demo");
  await expect(scope).toHaveText("fastapi-auth-demo");
  await expect(page).toHaveURL(urlA);
  await expect(page.getByTestId("message")).toHaveCount(1);
  await expect(page.getByTestId("message")).toContainText(QUESTION);
  await expect(page.getByTestId("answer")).toBeVisible();
  await expect(page.getByTestId("message")).not.toContainText("How does the auth module verify tokens?");

  // Reload: still restored, from storage.
  await page.reload();
  await expect(scope).toHaveText("fastapi-auth-demo");
  await expect(page).toHaveURL(urlA);
  await expect(page.getByTestId("message")).toHaveCount(1);
  await expect(page.getByTestId("message").getByTestId("answer")).toContainText("bearer");
  await expect(page.getByTestId("step-summary")).toHaveText(/\d+ steps/);

  // Start a newer chat, then open the older one from the list and continue it.
  await page.getByTestId("new-chat").click();
  await expect(page.getByTestId("empty-state")).toBeVisible();
  await askInComposer(page, "Where does the API read the JWT signing secret from?");
  const items = page.getByTestId("conversation-item");
  await expect(items.first()).toContainText("Where does the API read the JWT");
  await items.filter({ hasText: QUESTION.slice(0, 30) }).first().getByRole("button").first().click();
  await expect(page).toHaveURL(urlA);
  await expect(page.getByTestId("message")).toHaveCount(1);
  await askInComposer(page, "Which of the two causes should I fix first?");
  await expect(page.getByTestId("message")).toHaveCount(2);
  await page.reload();
  await expect(page.getByTestId("message")).toHaveCount(2);
});

test("rename and delete a conversation (with confirmation)", async ({ page }) => {
  await ready(page);
  await askInComposer(page, "How does the server parse the Authorization header?");
  const item = page.locator('[data-testid="conversation-item"][data-active]');
  await item.hover();
  await item.getByRole("button", { name: /^Rename/ }).click();
  await page.getByLabel("Conversation title").fill("Header parsing");
  await page.getByLabel("Conversation title").press("Enter");
  await expect(page.locator('[data-testid="conversation-item"][data-active]')).toContainText("Header parsing");
  await expect(page.getByTestId("conversation-scope")).toContainText("Header parsing");

  const renamed = page.locator('[data-testid="conversation-item"][data-active]');
  await renamed.hover();
  await renamed.getByRole("button", { name: /^Delete/ }).click();
  const confirm = page.getByRole("alertdialog", { name: "Delete conversation" });
  await expect(confirm).toContainText('Delete "Header parsing"?');
  await confirm.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByTestId("conversation-item").filter({ hasText: "Header parsing" })).toHaveCount(0);
});
