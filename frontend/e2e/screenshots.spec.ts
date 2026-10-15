import { expect, type Page, test } from "@playwright/test";
import { go, QUESTION, ready } from "./helpers";

// Regenerates docs/images/*.png against the demo backend: npm run screenshots
const OUT = "../docs/images";

test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });

async function shot(page: Page, name: string) {
  await page.mouse.move(0, 0);
  await page.waitForTimeout(450);
  await page.screenshot({ path: `${OUT}/${name}.png` });
}

for (const scheme of ["light", "dark"] as const) {
  test(`screenshots ${scheme}`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem("theme", t), scheme);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await ready(page);
    await shot(page, `${scheme}-assistant-empty`);

    await page.getByTestId("example-question").filter({ hasText: QUESTION }).click();
    await expect(page.getByTestId("step-timeline").locator("li").nth(2)).toBeVisible();
    await expect(page.getByTestId("ctx-github-status")).toHaveText("Waiting");
    await page.screenshot({ path: `${OUT}/${scheme}-assistant-running.png` });

    await expect(page.getByTestId("answer")).toBeVisible({ timeout: 30_000 });
    await shot(page, `${scheme}-assistant-answer`);
    await page.getByTestId("answer-actions").scrollIntoViewIfNeeded();
    await page.getByTestId("thread").evaluate((el) => el.scrollBy(0, 240));
    await shot(page, `${scheme}-assistant-sources`);

    await page.keyboard.press("Control+k");
    await page.keyboard.type("bearer");
    await expect(page.getByText("Answers in this session")).toBeVisible();
    await expect(page.getByText(/GitHub · fastapi/)).toBeVisible();
    await shot(page, `${scheme}-command-palette`);
    await page.keyboard.press("Escape");

    await page.getByTestId("prepare-issue").click();
    await expect(page.getByTestId("draft-drawer")).toBeVisible();
    await page.getByLabel("Type the repository name to confirm").fill("demo-org/sandbo");
    await shot(page, `${scheme}-approval-drawer`);
    await page.getByRole("button", { name: "Cancel", exact: true }).click(); // never approve
    await expect(page.getByText("Draft cancelled")).toBeHidden({ timeout: 10_000 });

    await page.getByTestId("open-source").click();
    await expect(page.locator("[data-highlighted]").first()).toBeVisible();
    await shot(page, `${scheme}-code-explorer`);

    await go(page, "Overview");
    await shot(page, `${scheme}-overview`);
    await go(page, "Knowledge Base");
    await expect(page.getByTestId("kb-files")).toBeVisible();
    await shot(page, `${scheme}-knowledge-base`);
    await go(page, "GitHub Issues");
    await page.getByTestId("issue-list").getByRole("button").first().click();
    await expect(page.getByTestId("issue-detail")).toBeVisible();
    await shot(page, `${scheme}-github-issues`);
    await go(page, "Pull Requests");
    await page.getByTestId("pull-list").getByRole("button").first().click();
    await expect(page.getByTestId("pull-files")).toBeVisible();
    await shot(page, `${scheme}-pull-requests`);
    await go(page, "Activity");
    await page.getByTestId("activity-item").first().click();
    await shot(page, `${scheme}-activity`);
    await go(page, "Settings");
    await shot(page, `${scheme}-settings`);
  });
}
