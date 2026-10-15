import { expect, test } from "@playwright/test";
import { QUESTION, ready } from "./helpers";

// Regenerates docs/images/*.png against the demo backend: npm run screenshots
const OUT = "../docs/images";

test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });

for (const scheme of ["dark", "light"] as const) {
  test(`screenshots ${scheme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
    await ready(page);
    await page.screenshot({ path: `${OUT}/${scheme}-empty.png` });

    await page.getByText(QUESTION).first().click();
    await expect(page.getByTestId("step-timeline").locator("li").nth(2)).toBeVisible();
    await page.screenshot({ path: `${OUT}/${scheme}-in-progress.png` });

    await expect(page.getByTestId("answer")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("citation-S1").first().click();
    await page.mouse.move(0, 0);
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/${scheme}-answer.png` });

    await page.getByRole("tab", { name: /Trace/ }).click();
    await page.screenshot({ path: `${OUT}/${scheme}-trace.png` });

    await page.getByTestId("prepare-issue").click();
    await expect(page.getByTestId("draft-drawer")).toBeVisible();
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/${scheme}-draft.png` });
    await page.getByRole("button", { name: "Cancel", exact: true }).click(); // never approve
  });
}
