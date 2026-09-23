import { expect, test, type Page } from "@playwright/test";

const adminToken = process.env.ROUTER_ADMIN_TOKEN;
const live = process.env.RUN_LIVE_E2E === "1";

function captureConsoleErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

test("voice workspace is usable without developer guidance", async ({ page }) => {
  const errors = captureConsoleErrors(page);
  await page.goto("/voice/");
  await expect(page.getByRole("button", { name: "Talk to Butaq" })).toBeVisible();
  await expect(page.getByPlaceholder("Or type your message…")).toBeVisible();
  await expect(page.locator("body")).not.toHaveCSS("overflow-x", "scroll");
  expect(errors).toEqual([]);
});

test("admin exposes the complete settings-driven catalogue import", async ({ page }) => {
  test.skip(!adminToken, "ROUTER_ADMIN_TOKEN is required for admin browser QA");
  const errors = captureConsoleErrors(page);
  await page.goto("/admin/import/");
  await page.getByLabel("Router admin token").fill(adminToken!);
  await page.getByRole("button", { name: /Continue with token/i }).click();
  await expect(page.getByRole("heading", { name: "Import catalogue files" })).toBeVisible();
  // The intentional unauthenticated /auth/me probe returns 403 before login.
  errors.splice(0);
  for (const name of [
    "scenarios",
    "slots",
    "actions",
    "knowledge_base",
    "mock_backend",
    "dev_utterances",
    "dialogs_sample",
  ]) {
    await expect(page.locator(`input[name="${name}"]`)).toHaveCount(1);
  }
  await page.waitForTimeout(100);
  expect(errors).toEqual([]);
});

test("live text turn renders routing and workflow trace", async ({ page }) => {
  test.skip(!live, "Set RUN_LIVE_E2E=1 to make the paid end-to-end model call");
  const errors = captureConsoleErrors(page);
  await page.goto("/voice/");
  await page.getByPlaceholder("Or type your message…").fill("Хочу оформить ОГПО");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByLabel("Routing trace")).toContainText("SC02", {
    timeout: 90_000,
  });
  await expect(page.getByLabel("Routing trace")).toContainText("Scenario workflow");
  await expect(page.getByLabel("Routing trace")).toContainText("Routing");
  expect(errors).toEqual([]);
});
