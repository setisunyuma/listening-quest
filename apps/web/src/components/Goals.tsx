"use client";
// 統合指示書 P01: main goal, transition period and the proposal to move on. The decision is the learner's own;
// the judgement itself is made on the server (never in the browser).
import { useState } from "react";
import { useApp } from "@/components/Shell";
import { Msg, useData } from "@/components/ui";
import { api, ApiError, MODE_LABEL, type Json } from "@/lib/api";

const ROLE_JA: Record<string, string> = { current: "現在の主目標", trial: "次段階を体験中", maintained: "維持中（復習は継続）" };

function newId() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID().replace(/-/g, "") : `${Date.now()}${Math.random().toString(16).slice(2)}`;
}

export function useGoals() {
  return useData<Json>(() => api("/goals"), []);
}

export function GoalPanel({ full = false }: { full?: boolean }) {
  const app = useApp();
  const { data: g, reload } = useGoals();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  if (!g) return null;
  const p = g.proposal;
  const decide = async (decision: string, to_mode?: string) => {
    setBusy(true); setMsg(null);
    try {
      await api("/goals/decision", { body: { event_id: newId(), decision, to_mode } });
      await app.saveSettings({}); // the main goal may have changed on the server
      setMsg(decision === "adopt" ? "主目標を変更しました。これまでの段階の期限復習は日課の中で続きます。" : decision === "defer" ? "後で判断します。1週間ほどして、もう一度見通しを表示します。" : decision === "keep" ? "現在の目標を続けます。" : "変更しました。");
    } catch (e) { setMsg((e as ApiError).detail || (e as Error).message); }
    setBusy(false);
    reload();
  };
  const tr = g.transition;
  const missing: Json[] = p.readiness.missing ?? [];
  return (
    <section className={`panel${p.state === "recommended" ? " accent" : ""}`} aria-labelledby="goal-h" data-testid="goal-panel">
      <h2 id="goal-h">現在の主目標: {g.current.label}</h2>
      {msg && <Msg kind="ok">{msg}</Msg>}
      {tr.active && <p className="note"><strong>移行期 {tr.day}/{tr.of} 学習日</strong>：{MODE_LABEL[tr.from_mode] ?? tr.from_mode} の期限復習と橋渡しも日課に入ります。</p>}
      {p.state === "recommended" && g.next_mode ? (
        <div>
          <p><strong>{g.next_label}</strong> を主目標にすることを提案できます。</p>
          <ul className="note">{p.readiness.met.slice(0, 4).map((x: string) => <li key={x}>{x}</li>)}</ul>
          <p className="note">{p.adds_ja}。{p.keeps_ja}</p>
          <div className="row" role="group" aria-label="主目標の移行">
            <button className="primary" onClick={() => decide("adopt", g.next_mode)} disabled={busy}>いま移る</button>
            <button onClick={() => decide("defer")} disabled={busy}>後で判断する</button>
            <button className="ghost" onClick={() => decide("keep")} disabled={busy}>現在の目標を続ける</button>
          </div>
        </div>
      ) : g.next_mode && (full || p.state === "deferred") ? (
        <p className="note">{g.next_label}への見通し: {p.state === "deferred" ? `保留中（${p.deferred_until} ごろ再確認）` : `不足している証拠 ${missing.length} 件`}</p>
      ) : null}
      {full && g.next_mode && missing.length > 0 && (
        <ul className="note" aria-label="不足している証拠">{missing.map((x) => <li key={x.code}>{x.label_ja}</li>)}</ul>
      )}
      {full && (
        <>
          <h3>目標ごとの状態</h3>
          <div className="table-wrap"><table className="fig">
            <thead><tr><th scope="col">目標</th><th scope="col">状態</th><th scope="col">期日</th><th scope="col">今週の期限復習</th></tr></thead>
            <tbody>{g.goals.map((r: Json) => (
              <tr key={r.mode}><td>{r.label}</td><td>{r.role ? ROLE_JA[r.role] : "—"}</td><td>{r.target_date ?? r.date_note_ja}</td><td>{r.due_reviews_week}</td></tr>
            ))}</tbody>
          </table></div>
          {g.next_mode && p.state !== "recommended" && (
            <div className="row" style={{ marginTop: 8 }}>
              <button onClick={() => decide("trial", g.next_mode)} disabled={busy}>{g.next_label}を先に体験する</button>
            </div>
          )}
          <p className="faint">{g.note_ja} 判定の版: {g.version}。主目標はいつでも上のモード切替で手動で変えられ、履歴・復習・解放済みの課題は消えません。</p>
        </>
      )}
    </section>
  );
}
