import { expect, test } from "@playwright/test";
import { checkTouchLayout, login, setMode } from "./helpers";

test("login is required and wrong passwords are refused", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel("ユーザー名").fill("demo");
  await page.getByLabel("パスワード").fill("wrong-password-1");
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.locator("p[role=alert]")).toContainText("ユーザー名またはパスワードが違います");
  await login(page);
});

test("main screens render and the five modes switch everywhere", async ({ page, isMobile }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await login(page);
  await expect(page.getByText("デモ領域")).toBeVisible();
  await expect(page.getByRole("group", { name: "モード切替" }).getByRole("button")).toHaveText(["共通テスト", "英検準1級", "英検1級", "C1", "C2"]);
  const nav = page.getByRole("navigation", { name: "メイン" });
  for (const [link, heading] of [["教材", "教材"], ["ラジオ", "ラジオ"], ["成長", "成長"]]) {
    await nav.getByRole("link", { name: link, exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    await checkTouchLayout(page);
  }
  const more = async (name: string) => {
    if (isMobile) { await nav.getByRole("link", { name: "メニュー" }).click(); await page.getByRole("link", { name }).click(); } else await nav.getByRole("link", { name, exact: true }).click();
  };
  await more("履歴");
  await expect(page.getByRole("heading", { name: "解答の履歴" })).toBeVisible();
  await more("設定");
  await expect(page.getByRole("heading", { name: "日課" })).toBeVisible();
  await expect(page.getByLabel("1日の目標時間（5モード合計）")).toHaveValue("30");
  await expect(page.getByLabel("タイムゾーン")).toHaveValue("Asia/Tokyo");
  await expect(page.getByLabel("学習日の切り替え時刻")).toHaveValue("4");
  await expect(page.getByLabel("効果音（達成時の短い音）")).not.toBeChecked();
  await checkTouchLayout(page);
  await more("教材管理");
  await expect(page.getByRole("heading", { name: "有料生成と予算" })).toBeVisible();
  await checkTouchLayout(page);

  await nav.getByRole("link", { name: "教材", exact: true }).click();
  await expect(page.getByText(/共通テスト の教材 \d+ 件/)).toBeVisible();
  await expect(page.getByText("音声動作確認用").first()).toBeVisible(); // tone audio is labelled, never passed off as real material
  for (const [mode, part] of [["英検準1級", "Part 3 Real-Life"], ["英検1級", "Part 4 インタビュー（録音）"], ["C1", "講義"], ["C2", "含意"], ["共通テスト", "第5問 講義・追加発言・会話と図表の統合"]]) {
    await setMode(page, mode);
    await expect(page.getByText(new RegExp(`${mode} の教材 \\d+ 件`))).toBeVisible();
    await expect(page.locator("details.group summary", { hasText: part }).first()).toBeVisible();
  }
  await page.getByLabel("難度").selectOption("5");
  await expect(page.getByText("条件に合う教材がありません。")).toBeVisible();
  expect(errors).toEqual([]);
});

test("empty states explain what to do, and nothing is offered as playable", async ({ page }) => {
  await login(page, "owner", "owner-password-123"); // live area: no material, paid generation off
  await expect(page.getByText("このモードには、まだ学習できる教材がありません。").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "今日の学習を始める" })).toHaveCount(0);
  await page.goto("/library");
  await expect(page.getByText("このモードには、まだ学習できる教材がありません。")).toBeVisible();
  await page.goto("/radio");
  await page.getByRole("button", { name: "番組を作って聞く" }).click();
  await expect(page.getByText("ラジオで聞ける教材がまだありません")).toBeVisible();
  await page.goto("/check");
  await expect(page.getByText("開始できるセットがありません。")).toBeVisible();
  await page.goto("/admin");
  await expect(page.getByText("未設定（取り込みと手動承認は利用できます）")).toBeVisible();
  await expect(page.getByLabel(/有料生成を有効にする/)).not.toBeChecked(); // off by default
  await expect(page.getByLabel("1日の予算（USD）")).toHaveValue("1");
});
