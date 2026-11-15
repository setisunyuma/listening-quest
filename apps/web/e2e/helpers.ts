import { expect, type Page } from "@playwright/test";

export async function login(page: Page, user = "demo", pw = "demo-password-123") {
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(user);
  await page.getByLabel("パスワード").fill(pw);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByRole("heading", { name: "今日のクエスト" })).toBeVisible();
}

export async function setMode(page: Page, label: string) {
  await page.getByRole("group", { name: "モード切替" }).getByRole("button", { name: label, exact: true }).click();
  await expect(page.getByRole("group", { name: "モード切替" }).getByRole("button", { name: label, exact: true })).toHaveAttribute("aria-pressed", "true");
}

/** No horizontal page scroll and every visible button / link-button is at least 44px tall. */
export async function checkTouchLayout(page: Page) {
  const r = await page.evaluate(() => {
    const small: string[] = [];
    for (const el of Array.from(document.querySelectorAll("button, a.btn, nav.tabs a, select, input:not([type=checkbox]):not([type=file]):not([hidden])"))) {
      const b = (el as HTMLElement).getBoundingClientRect();
      if (b.width > 0 && b.height > 0 && b.height < 43.5) small.push(`${el.tagName}:${(el.textContent || "").trim().slice(0, 20)}:${Math.round(b.height)}`);
    }
    // form controls never stick out of the panel they are in (a file input's own width once did on iPhone)
    const outside: string[] = [];
    for (const el of Array.from(document.querySelectorAll("input, select, textarea"))) {
      const b = (el as HTMLElement).getBoundingClientRect();
      const box = (el as HTMLElement).closest(".panel");
      if (!box || b.width === 0) continue;
      if (b.right > box.getBoundingClientRect().right - 1) outside.push(`${el.tagName}:${(el as HTMLInputElement).type}:${Math.round(b.right - box.getBoundingClientRect().right)}px`);
    }
    return { overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth, small, outside };
  });
  expect(r.outside, "form controls outside their panel").toEqual([]);
  expect(r.overflow, "page must not scroll sideways").toBeLessThanOrEqual(1);
  expect(r.small, "tap targets under 44px").toEqual([]);
}
