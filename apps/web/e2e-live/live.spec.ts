import { expect, test } from "@playwright/test";

// Real generated audio: play it to the end, answer, read the explanation, check it was saved.
test("real audio unit: play, answer, explanation, saved", async ({ page }) => {
  const shots = `${__dirname}/../../../var/verify`;
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill("verifier");
  await page.getByLabel("パスワード").fill(process.env.LQ_VERIFY_PASSWORD!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByRole("heading", { name: "今日のクエスト" })).toBeVisible();
  await page.goto("/library");
  const card = page.locator("a.card").first();
  await expect(card).toBeVisible();
  if (!process.env.LQ_VERIFY_REHEARSAL) await expect(page.getByText("音声動作確認用")).toHaveCount(0); // real speech, not demo tones
  await card.click();
  await expect(page.getByText("初見として記録")).toBeVisible();
  await page.screenshot({ path: `${shots}/1-question.png`, fullPage: true });

  const audio = page.getByRole("button", { name: /再生/ }).first();
  await audio.click();
  await expect(page.getByText(/・再生中/).first()).toBeVisible();
  const t0 = Date.now();
  await expect(page.getByText(/・再生終了/).first()).toBeVisible({ timeout: 150_000 }); // played to the end
  const seconds = (Date.now() - t0) / 1000;
  expect(seconds, "speech lasts several seconds").toBeGreaterThan(process.env.LQ_VERIFY_REHEARSAL ? 0.5 : 3);

  const groups = page.getByRole("group", { name: /問\d+の選択肢/ });
  for (let i = 0; i < (await groups.count()); i++) await groups.nth(i).getByRole("button").first().click();
  await page.getByRole("button", { name: "解答する" }).click();
  await expect(page.getByText(/\d+ \/ \d+ 正解/)).toBeVisible();
  await expect(page.getByText("要点:").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "根拠の音声を再生" }).first()).toBeVisible();
  await page.screenshot({ path: `${shots}/2-result.png`, fullPage: true });
  await page.getByRole("button", { name: "▶ 正答と解説を音声で聞く" }).click();
  await expect(page.getByRole("button", { name: "❚❚ 解説音声を止める" })).toBeVisible();
  await page.getByRole("button", { name: "❚❚ 解説音声を止める" }).click();
  await page.getByRole("button", { name: "解説を確認した" }).click();
  await expect(page.getByText("+10 XP")).toBeVisible();

  await page.goto("/history");
  await expect(page.locator("table.list tbody tr")).toHaveCount(1);
  await expect(page.getByText("初見成績に算入")).toBeVisible();
  await page.screenshot({ path: `${shots}/3-history.png`, fullPage: true });
});
