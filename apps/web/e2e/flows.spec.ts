import { expect, test, type Page } from "@playwright/test";
import { checkTouchLayout, login, setMode } from "./helpers";

test.describe.configure({ mode: "serial" });

async function answerAll(page: Page, pick: "first" | "skipLast" = "first") {
  const groups = page.getByRole("group", { name: /問\d+の選択肢/ });
  const n = await groups.count();
  for (let i = 0; i < n; i++) {
    if (pick === "skipLast" && i === n - 1) await page.getByRole("button", { name: "この問題をスキップ" }).nth(i).click();
    else await groups.nth(i).getByRole("button").first().click();
  }
  return n;
}

test("quest: play, answer, grading, explanation, XP, and the next unit", async ({ page }) => {
  await login(page);
  await expect(page.getByText(/目標 30分 →/)).toBeVisible();
  await page.getByRole("button", { name: "クエストを開始" }).click();
  await expect(page.getByRole("img", { name: /今日の進捗 0 \/ \d+/ })).toBeVisible();
  await checkTouchLayout(page);
  const total = Number((await page.getByRole("img", { name: /今日の進捗/ }).getAttribute("aria-label"))!.split("/")[1]);
  await page.getByRole("link", { name: "開始する" }).click();

  // the answering screen never contains answers or explanations
  await expect(page.getByRole("button", { name: /再生/ }).first()).toBeVisible();
  await expect(page.getByText("初見として記録")).toBeVisible();
  await expect(page.getByText("音声動作確認用").first()).toBeVisible();
  expect(await page.content()).not.toMatch(/要点:|Answer:|正答/);
  const submit = page.getByRole("button", { name: "解答する" });
  await expect(submit).toBeDisabled();

  // real playback: the label follows the audio element's own events
  await page.getByRole("button", { name: /再生/ }).first().click();
  await expect(page.getByText(/再生済み 1 回/).first()).toBeVisible();
  await expect(page.getByText(/・再生中|・再生終了/).first()).toBeVisible();
  await checkTouchLayout(page);

  await answerAll(page);
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect(page.getByRole("heading", { name: /結果/ })).toBeVisible();
  await expect(page.getByText(/\d+ \/ \d+ 正解/)).toBeVisible();
  await expect(page.getByText(/○ 正解|× 不正解/).first()).toBeVisible(); // label + icon, not colour alone
  await expect(page.getByText("要点:").first()).toBeVisible();
  await expect(page.getByText(/次の復習: \d{4}-\d{2}-\d{2}（初回学習の翌日に最初の復習）/)).toBeVisible();
  await expect(page.getByRole("button", { name: "根拠の音声を再生" }).first()).toBeVisible();
  await page.getByRole("button", { name: "解説を確認した" }).click();
  await expect(page.getByText("+10 XP")).toBeVisible();
  await page.reload(); // reload keeps the graded result; no second reward
  await expect(page.getByRole("heading", { name: /結果/ })).toBeVisible();
  await page.getByRole("button", { name: "解説を確認した" }).click();
  await expect(page.getByText("この教材の今日の基本XPは獲得済みです。")).toBeVisible();
  await page.getByRole("button", { name: "次へ進む" }).click();
  await expect(page).toHaveURL(/\/play\?unit=.*slot=/);
  await page.goto("/");
  await expect(page.getByRole("img", { name: `今日の進捗 1 / ${total}` })).toBeVisible(); // the plan did not grow
  await expect(page.getByRole("link", { name: "再開する" })).toBeVisible();
  await expect(page.getByText(/XP（今日 \+10）/)).toBeVisible();
});

test("skipping leaves the slot open and aids are recorded as assisted", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "再開する" }).click();
  await page.getByText("学習の補助を使う").click();
  await page.getByRole("button", { name: "英文を表示" }).click();
  await expect(page.getByLabel("英文")).toBeVisible();
  await expect(page.getByText("練習として記録")).toBeVisible();
  const n = await answerAll(page, "skipLast");
  await page.getByRole("button", { name: "解答する" }).click();
  await expect(page.getByText(/記録の種類: (一部未回答|補助付き)/)).toBeVisible();
  await page.getByRole("button", { name: "解説を確認した" }).click();
  if (n === 1) await expect(page.getByText("未回答があるため、この枠は未完了です")).toBeVisible();
  await page.goto("/history");
  await expect(page.getByText("英文表示").first()).toBeVisible();
});

test("a failed audio request is shown as an error, never as playing", async ({ page }) => {
  await login(page);
  await page.goto("/library");
  await page.locator("a.card.new").first().click();
  await page.route("**/api/audio/**", (r) => r.abort());
  await page.getByRole("button", { name: /再生/ }).first().click();
  await expect(page.locator("p[role=alert]")).toContainText(/音声を(読み込めません|再生できません)/);
  await expect(page.getByText(/再生済み 0 回/).first()).toBeVisible();
  await expect(page.getByText("・再生中")).toHaveCount(0);
  await page.unroute("**/api/audio/**");
  await page.getByRole("button", { name: "再試行" }).click();
  await expect(page.getByText(/再生済み 1 回/).first()).toBeVisible();
});

test("offline: answer a saved unit, survive a restart, grade once after sync", async ({ page, context }) => {
  await login(page);
  await page.getByRole("button", { name: "未開始分をオフライン用に保存" }).click();
  await expect(page.getByText(/件の問題と音声を端末に保存しました/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("heading", { name: "端末に保存した問題" })).toBeVisible();
  await expect(page.getByText("オフライン用に予約").first()).toBeVisible();
  await page.reload(); // from here the service worker controls the page and has the app shell
  await page.waitForFunction(() => navigator.serviceWorker.controller !== null);
  await expect(page.getByRole("heading", { name: "端末に保存した問題" })).toBeVisible();
  await page.goto("/play"); await page.goto("/"); // make sure the player's code is cached
  await expect(page.getByRole("heading", { name: "端末に保存した問題" })).toBeVisible();

  await context.setOffline(true);
  await page.reload();
  await expect(page.getByText("オフラインです。端末に保存した問題に解答できます。")).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "オフライン" }).first()).toBeVisible();
  await page.getByRole("link", { name: "解く" }).first().click();
  await expect(page.getByText("オフライン解答")).toBeVisible();
  await page.getByRole("button", { name: /再生/ }).first().click();
  await expect(page.getByText(/再生済み 1 回/).first()).toBeVisible(); // plays from the verified local copy
  await answerAll(page);
  await page.getByRole("button", { name: "解答を保存する（採点は同期後）" }).click();
  await expect(page.getByRole("heading", { name: "解答済み・採点待ち" })).toBeVisible();
  await expect(page.getByText("まだ採点されていません。")).toBeVisible();
  expect(await page.content()).not.toMatch(/正解|要点:/);
  await page.goto("/"); // "restart": the unsent answer is still on the device
  await expect(page.getByText("未同期 1件")).toBeHidden(); // offline badge takes priority
  await expect(page.getByText("解答済み・採点待ち: 1件")).toBeVisible();

  await context.setOffline(false);
  await page.reload();
  await expect(page.getByText(/オフラインの回答 1 件を採点しました/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("同期済み")).toBeVisible();
  await page.goto("/history");
  await expect(page.getByText("オフライン解答").first()).toBeVisible();
  await expect(page.getByText("オフライン解答")).toHaveCount(1); // graded exactly once
});

test("generation: queued job becomes available in the list without reloading; failures show a reason", async ({ page }) => {
  await login(page);
  await setMode(page, "C2");
  await page.goto("/library");
  const countText = await page.getByText(/C2 の教材 \d+ 件/).textContent();
  const before = Number(countText!.match(/(\d+) 件/)![1]);
  const adminTab = await page.context().newPage();
  await adminTab.goto("/admin");
  await adminTab.getByLabel("形式").selectOption("implicature");
  await adminTab.getByRole("button", { name: "生成を予約" }).click();
  await expect(adminTab.getByText("1件の生成を予約しました。")).toBeVisible();
  await expect(page.getByText(`C2 の教材 ${before + 1} 件`)).toBeVisible({ timeout: 45_000 }); // the other tab picks it up by polling
  // the failed blind review prepared by the test data is visible with its reason
  const failed = adminTab.locator("tr", { hasText: "失敗" }).first();
  await expect(failed).toContainText("隔離");
  await expect(failed).toContainText("blind review disagreed");
  await expect(adminTab.locator("tr", { hasText: "隔離" }).filter({ hasText: "デモ用の模擬検品（不一致）" }).first()).toBeVisible();
  await checkTouchLayout(adminTab);
  await adminTab.close();
  await setMode(page, "共通テスト");
});

test("radio: continuous program, real coverage, self report", async ({ page }) => {
  await login(page);
  await page.goto("/radio");
  await page.getByLabel("長さの目安").selectOption("5");
  await page.getByRole("button", { name: "番組を作って聞く" }).click();
  await expect(page.getByRole("heading", { name: /ラジオ再生/ })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "▶ 再生" }).click();
  await expect(page.getByText(/再生中/).first()).toBeVisible();
  await checkTouchLayout(page);
  await page.getByRole("button", { name: "自分では正解" }).first().click();
  await expect(page.getByRole("button", { name: "✓ 自分では正解" })).toBeVisible();
  await page.getByRole("button", { name: "オフライン用にダウンロード" }).click();
  await expect(page.getByText("✓ 端末に保存済み（オフライン再生可）")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "❚❚ 一時停止" }).click();
  await expect(page.getByText(/一時停止中/)).toBeVisible();
  await page.goto("/history");
  await expect(page.getByText("自分では正解").first()).toBeVisible();
  await expect(page.getByText("客観採点とは別の記録").first()).toBeVisible();
});

test("assessment: duration shown before start, no explanations until submitted", async ({ page }) => {
  await login(page);
  await setMode(page, "C1");
  await page.goto("/check");
  await expect(page.getByRole("heading", { name: /本番条件のアプリ内実力チェック/ })).toBeVisible();
  await expect(page.getByText(/7問・3 Unit・所要時間/)).toBeVisible();
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "開始する" }).click();
  await expect(page.getByRole("button", { name: "音声を開始する" })).toBeVisible();
  expect(await page.content()).not.toMatch(/要点:|Answer:/);
  await page.getByRole("button", { name: "音声を開始する" }).click();
  await expect(page.getByText(/♪ 再生中|読み込み中/).first()).toBeVisible();
  const groups = page.getByRole("group", { name: /解答欄\d+/ });
  for (let i = 0; i < 6; i++) await groups.nth(i).getByRole("button").first().click();
  await expect(page.getByText("6 / 7 回答")).toBeVisible();
  page.once("dialog", (d) => { expect(d.message()).toContain("未回答が 1 問"); d.accept(); });
  await page.getByRole("button", { name: "提出して採点する" }).click();
  await expect(page.getByRole("heading", { name: "アプリ内実力チェックの結果" })).toBeVisible();
  await expect(page.getByText(/C1 \d \/ 7/).first()).toBeVisible();
  await expect(page.getByText("－ 未回答")).toBeVisible();
  await expect(page.getByText("CEFRの認定や公式試験の結果を示すものではありません")).toBeVisible();
  await setMode(page, "共通テスト");
});

test("feedback: mark a line as missed, replay it from the list, and report a unit", async ({ page }) => {
  await login(page);
  await page.goto("/library");
  await page.locator('a.card[href^="/play?unit="]').first().click();
  await expect(page.getByRole("group", { name: /問\d+の選択肢/ }).first()).toBeVisible();
  await answerAll(page);
  await page.getByRole("button", { name: "解答する" }).click();
  await expect(page.getByRole("heading", { name: /結果/ })).toBeVisible();
  const miss = page.getByRole("button", { name: "聞き取れなかった", exact: true }).first();
  await miss.click();
  await expect(page.getByRole("button", { name: "✓ 聞き取れなかった" }).first()).toBeVisible();
  await checkTouchLayout(page);
  // glossary, line playback, hidden English and the Japanese rendering (demo material carries sample notes)
  await page.getByText("英文全体を見る").click();
  await page.getByRole("button", { name: "英文を隠す（タップで表示）" }).click();
  const hidden = page.getByRole("button", { name: /（タップで英文を表示）/ });
  const n = await hidden.count();
  expect(n).toBeGreaterThan(0);
  await hidden.first().click();
  await expect(hidden).toHaveCount(n - 1);
  if (await page.getByRole("button", { name: "和訳を表示" }).count()) {  // units generated with study notes
    await page.getByRole("button", { name: "和訳を表示" }).click();
    await expect(page.locator(".evidence .faint").first()).toBeVisible();
  }
  await expect(page.getByRole("button", { name: "この文を再生" }).first()).toBeVisible();
  await checkTouchLayout(page);
  await page.getByText("この教材を報告する").click();
  await page.getByRole("group", { name: "報告の種類" }).getByRole("button", { name: "意味・解説が分かりにくい" }).click();
  await page.getByLabel("メモ（任意）").fill("e2e: 背景が分からない");
  await page.getByRole("button", { name: "報告を送る" }).click();
  await expect(page.getByText(/報告しました。教材管理の「報告」に記録されます。/)).toBeVisible();

  await page.goto("/missed");
  await expect(page.getByRole("heading", { name: /聞き取れなかった文 1 文/ })).toBeVisible();
  await page.getByRole("button", { name: "英文を表示" }).click();
  await expect(page.locator(".qbox p.en")).toBeVisible();
  await checkTouchLayout(page);
  await page.getByRole("button", { name: "書き取る" }).click();
  await page.getByLabel("聞こえたとおりに英語で入力").fill("the");
  await page.getByRole("button", { name: "採点" }).click();
  await expect(page.getByText(/\d+ \/ \d+ 語/)).toBeVisible();
  await page.getByRole("button", { name: "聞き取れた" }).click();
  await expect(page.getByText("まだありません。")).toBeVisible();

  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: /報告 1 件/ })).toBeVisible();
  await expect(page.getByText("e2e: 背景が分からない")).toBeVisible();
  await page.getByRole("button", { name: "対応済みにする" }).click();
  await expect(page.getByRole("heading", { name: /報告 1 件/ })).toHaveCount(0);
});

test("settings: unsent answers are announced before logout clears the device", async ({ page, context }) => {
  await login(page);
  await page.goto("/settings");
  await page.getByLabel("主な操作ボタンの位置").selectOption("left");
  await expect(page.locator("body")).toHaveClass(/side-left/);
  await page.getByLabel("演出と背景の動きを減らす").click(); // saved on the server first, then reflected
  await expect(page.getByLabel("演出と背景の動きを減らす")).toBeChecked();
  await expect(page.locator("body")).toHaveClass(/reduce/);
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: "学習履歴と設定を書き出す（JSON）" }).click()]);
  expect(download.suggestedFilename()).toMatch(/listening-quest-export-\d+\.json/);
  await page.evaluate(async () => {
    const me = JSON.parse(localStorage.getItem("lq:me")!);
    await new Promise<void>((res, rej) => {
      const req = indexedDB.open(`lq-${me.user_id}`, 1);
      req.onsuccess = () => { const t = req.result.transaction("outbox", "readwrite"); t.objectStore("outbox").put({ event_id: "e2e-unsent-0001", unit_id: "missing", unit_revision: 1, answers: {}, plays: {}, rates: {}, client_started_at: "", client_answered_at: "", title: "t" }); t.oncomplete = () => res(); t.onerror = () => rej(t.error); };
    });
  });
  await context.setOffline(true);
  await page.getByRole("button", { name: /ログアウト/ }).click();
  await expect(page.getByRole("alertdialog")).toContainText("まだ送信していない回答が 1 件あります");
  await context.setOffline(false);
  await page.getByRole("button", { name: "削除してログアウト" }).click();
  await expect(page).toHaveURL(/\/login/);
  expect(await page.evaluate(async () => (await indexedDB.databases()).filter((d) => d.name?.startsWith("lq-")).length)).toBe(0);
  expect(await page.evaluate(async () => (await caches.keys()).filter((k) => k.startsWith("lq-audio-")).length)).toBe(0);
});
