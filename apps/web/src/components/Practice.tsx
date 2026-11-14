"use client";
// 統合指示書 G01 / M01: practice tasks - which are part of the day, what comes next, what has been done.
// The judgement is made on the server; "unlocked" means usable in the day, never a grade or CEFR level.
import { useState } from "react";
import { Msg, useData } from "@/components/ui";
import { api, ApiError, MODE_LABEL, type Json } from "@/lib/api";

export function PracticePanel({ full = false }: { full?: boolean }) {
  const { data, reload } = useData<Json>(() => api("/practice"), []);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!data) return null;
  const tasks: Json[] = data.tasks;
  const start = async (kind: string) => {
    setBusy(true); setMsg(null);
    try {
      const r = await api<Json>(`/practice/${kind}/start`, { body: {} });
      setMsg(r.added_today ? "今日の予定の未開始の枠と入れ替えました（時間は増えません）。" : "日課で使えるようにしました。今日の枠に空きがないため、次の学習日から入ります。");
    } catch (e) { setMsg((e as ApiError).detail || (e as Error).message); }
    setBusy(false);
    reload();
  };
  if (!full) {
    const next = tasks.find((t) => t.status === "next") ?? tasks.find((t) => t.status === "unlocked" && t.attempts === 0);
    if (!next) return null;
    return (
      <section className="panel" aria-labelledby="pr-next-h" data-testid="practice-next">
        <h2 id="pr-next-h">次に広がる練習</h2>
        <p>次は「{next.title_ja}」ができます。<small>{next.goal_ja}</small></p>
        <p className="faint">{next.status_ja}・{MODE_LABEL[next.stage]}の段階・1回 {next.minutes[0]}〜{next.minutes[1]}分。{data.note_ja}</p>
      </section>
    );
  }
  return (
    <section className="panel" aria-labelledby="pr-h" data-testid="practice-panel">
      <h2 id="pr-h">実践課題</h2>
      <p className="note">{data.note_ja} 試験形式の成績とは別に記録します。</p>
      {msg && <Msg kind="ok">{msg}</Msg>}
      {tasks.map((t) => (
        <details key={t.kind} className="group">
          <summary><span className="gt">{t.title_ja} <small>{MODE_LABEL[t.stage]}・{t.status_ja}</small></span><span className="chev" aria-hidden="true">▶</span></summary>
          <div className="body">
            <p>{t.goal_ja}</p>
            <p className="note">場面の例: {t.scene_ja}（1回 {t.minutes[0]}〜{t.minutes[1]}分）</p>
            {t.reason_ja && <p className="note">理由: {t.reason_ja}</p>}
            {t.can_do.length > 0 ? (
              <ul aria-label="できたこと">{t.can_do.map((c: Json) => <li key={c.label_ja}>{c.label_ja}を取り出せた（{c.n}回・最近 {c.last}）</li>)}</ul>
            ) : <p className="faint">できたことの記録: 未確認（まだ証拠になる回答がありません）</p>}
            {t.missing.length > 0 && <ul className="note" aria-label="不足している証拠">{t.missing.map((x: Json) => <li key={x.code}>{x.label_ja}</li>)}</ul>}
            {t.material_ja && <p className="faint">{t.material_ja}</p>}
            {t.status !== "unlocked" && t.status !== "consolidating" && (
              <button className="small" onClick={() => start(t.kind)} disabled={busy}>この練習を試す（支援付きで導入）</button>
            )}
          </div>
        </details>
      ))}
    </section>
  );
}
