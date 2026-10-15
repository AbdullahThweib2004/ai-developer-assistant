import { expect, test } from "@playwright/test";
import { ask401, go, QUESTION, ready } from "./helpers";

// Demo mode only: scripted LLM, fake embeddings, mocked GitHub. Never approves a draft.

test("every page is reachable from the sidebar and keeps the session", async ({ page }) => {
  await ready(page);
  const pages: [string, string][] = [
    ["Overview", "page-overview"], ["Knowledge Base", "page-knowledge"], ["Code Explorer", "page-code"],
    ["GitHub Issues", "issue-list"], ["Pull Requests", "pull-list"], ["Activity", "page-activity"], ["Settings", "page-settings"],
  ];
  for (const [label, testId] of pages) {
    await go(page, label);
    await expect(page.getByTestId(testId)).toBeVisible();
    await expect(page.getByRole("link", { name: label, exact: true })).toHaveAttribute("aria-current", "page");
  }
  await go(page, "AI Assistant");
  await expect(page.getByTestId("empty-state")).toBeVisible();
  await expect(page.getByTestId("project-select")).toContainText("fastapi-auth-demo");
});

test("investigation streams into the Investigation Context cards", async ({ page }) => {
  await ready(page);
  const rag = page.getByTestId("ctx-rag-status");
  const github = page.getByTestId("ctx-github-status");
  await expect(rag).toHaveText("Idle");
  await page.getByTestId("example-question").filter({ hasText: QUESTION }).click();
  await expect(rag).toHaveText(/Running|Completed/);
  await expect(github).toHaveText("Waiting");
  await expect(page.getByTestId("ctx-trace")).toContainText("search_project");
  await expect(github).toHaveText(/Running|Completed/, { timeout: 15_000 });
  await expect(page.getByTestId("answer")).toBeVisible({ timeout: 30_000 });
  await expect(rag).toHaveText("Completed");
  await expect(github).toHaveText("Completed");
  await expect(page.getByTestId("ctx-github")).toContainText("related issues");
  await expect(page.getByTestId("ctx-sources")).toContainText("auth.py");
  await expect(page.getByTestId("step-summary")).toHaveText(/\d+ steps · \d+ sources? · [\d.]+/);
  await expect(page.getByTestId("cause-1")).toContainText("Evidence");
  await expect(page.getByTestId("key-findings")).toContainText(/Lines \d+–\d+/);
});

test("Open source opens the Code Explorer at the cited lines", async ({ page }) => {
  await ready(page);
  await ask401(page);
  await page.getByTestId("open-source").click();
  await expect(page).toHaveURL(/\/code\?file=app%2Fauth\.py&start=(\d+)&end=(\d+)/);
  const url = new URL(page.url());
  const start = Number(url.searchParams.get("start"));
  const end = Number(url.searchParams.get("end"));
  await expect(page.getByTestId("viewer-path")).toHaveText("app/auth.py");
  await expect(page.getByTestId("viewer-range")).toHaveText(`Lines ${start}–${end}`);
  const highlighted = page.locator("[data-highlighted]");
  await expect(highlighted).toHaveCount(end - start + 1);
  await expect(highlighted.first()).toHaveAttribute("data-line", String(start));
  await expect(highlighted.first()).toBeInViewport();
});

test("command palette searches the index, GitHub and answers", async ({ page }) => {
  await ready(page);
  await ask401(page);
  await page.keyboard.press("Control+k");
  const palette = page.getByTestId("command-palette");
  await expect(palette).toBeVisible();
  await page.keyboard.type("bearer");
  await expect(palette.getByText("Project", { exact: true })).toBeVisible();
  await expect(palette.getByTestId("palette-item").filter({ hasText: "app/auth.py" }).first()).toBeVisible();
  await expect(palette.getByText(/GitHub · fastapi\/fastapi/)).toBeVisible();
  await expect(palette.getByText("Answers in this session")).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(palette).toBeHidden();
  await expect(page).toHaveURL(/\/code\?file=/);
});

test("Search GitHub issues runs a GitHub-only follow-up", async ({ page }) => {
  await ready(page);
  await ask401(page);
  await page.getByTestId("search-github").first().click();
  const second = page.getByTestId("message").nth(1);
  await expect(second).toContainText("GitHub only");
  await expect(second.getByTestId("answer")).toBeVisible({ timeout: 30_000 });
  await expect(second).toContainText("fastapi/fastapi#");
});

test("approval drawer: approve stays disabled until the exact repo is typed; cancel", async ({ page }) => {
  await ready(page);
  await ask401(page);
  await page.getByTestId("prepare-issue").click();
  const drawer = page.getByTestId("draft-drawer");
  await expect(drawer).toBeVisible();
  await expect(drawer.getByTestId("draft-repo")).toHaveText("demo-org/sandbox");
  const approve = drawer.getByTestId("approve-button");
  await expect(approve).toBeDisabled();
  const confirm = drawer.getByLabel("Type the repository name to confirm");
  for (const wrong of ["demo-org/sandbo", "Demo-Org/Sandbox", "fastapi/fastapi"]) {
    await confirm.fill(wrong);
    await expect(approve).toBeDisabled();
  }
  await confirm.fill("demo-org/sandbox");
  await expect(approve).toBeEnabled();
  await drawer.getByLabel("Issue title").fill("Edited title");
  await expect(confirm).toHaveValue(""); // editing clears the confirmation
  await expect(approve).toBeDisabled();
  await drawer.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(drawer).toBeHidden();
  await expect(page.getByText("Draft cancelled")).toBeVisible();
});
