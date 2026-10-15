import { expect, test } from "@playwright/test";
import { QUESTION, ready } from "./helpers";

// Demo mode only: scripted LLM, fake embeddings, mocked GitHub. Never approves a draft.
test("index, investigate the 401, inspect evidence, open and cancel a draft", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("index-button").click();
  await expect(page.getByTestId("index-label")).toBeVisible();
  await ready(page, false);

  await page.getByText(QUESTION).first().click();
  const timeline = page.getByTestId("step-timeline");
  await expect(timeline.getByText(/Searching project for/)).toBeVisible();
  await expect(timeline.getByText(/Reading app\/auth\.py/)).toBeVisible();
  await expect(page.getByTestId("answer")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("step-summary")).toHaveText(/\d+ steps · \d+ sources? · [\d.]+/);
  await expect(page.getByTestId("cause-1")).toContainText("Evidence");

  await page.getByTestId("citation-S1").first().click();
  const panel = page.getByTestId("evidence-panel");
  await expect(panel.getByTestId("source-card-S1")).toBeVisible();
  await expect(panel.getByTestId("source-card-S1")).toContainText("app/auth.py");

  await panel.getByRole("tab", { name: /Trace/ }).click();
  await expect(panel.getByTestId("trace-table")).toContainText("search_project");

  await page.getByTestId("prepare-issue").click();
  const drawer = page.getByTestId("draft-drawer");
  await expect(drawer).toBeVisible();
  await expect(drawer.getByTestId("draft-repo")).toHaveText("demo-org/sandbox");
  const approve = drawer.getByTestId("approve-button");
  await expect(approve).toBeDisabled();
  const confirm = drawer.getByLabel("Type the repository name to confirm");
  await confirm.fill("demo-org/sandbo");
  await expect(approve).toBeDisabled();
  await confirm.fill("Demo-Org/Sandbox");
  await expect(approve).toBeDisabled();
  await drawer.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(drawer).toBeHidden();
});
