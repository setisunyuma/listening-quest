"use client";
// L01 / L03 tasks of today's plan (U01): a short repair right after a problem, and the unaided check of the
// same lines (or chosen phrases) on a later day. Each ends after its steps whatever the outcome - no endless
// loop - and the results are kept apart from first-attempt scores.
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useApp } from "@/components/Shell";
import { Msg, useData } from "@/components/ui";
import { api, ApiError, type Json } from "@/lib/api";
import { playableUrl } from "@/lib/offline";
import { usePlayer } from "@/lib/usePlayer";

export default function TaskPage() {
  return <Suspense fallback={<p className="note" role="status">読み込み中…</p>}><TaskInner /></Suspense>;
}

function TaskInner() {
  const app = useApp();
  const router = useRouter();
  const slot = useSearchParams().get("slot") ?? "";
  const { data, error } = useData<Json>(() => api(`/tasks/${slot}`), [slot]);
  const player = usePlayer();
  const [active, setActive] = useState("");
  const [shown, setShown] = useState<Record<string, boolean>>({});
  const [finalPhase, setFinalPhase] = useState<Record<string, boolean>>({});
  const [res, setRes] = useState<Record<string, { ok: boolean; aided: boolean }>>({});
  const [msg, setMsg] = useState("");
  const [done, setDone] = useState<Json | null>(null);
  if (!data) return error ? <Msg>{error}</Msg> : <p className="note" role="status">読み込み中…</p>;
  const repair = data.kind === "repair";
  const play = async (it: Json, rate = 1, loop = false) => {
    if (!it.audio_url) return;
    setActive(it.item_id); player.setLoop(loop);
    await player.play((await playableUrl(app.me.user_id, it.audio_url)).src, rate, 0);
  };
  const reveal = (id: string) => setShown({ ...shown, [id]: true });
  // unaided means: in the final check (repair) or before answering (recall) the text was not visible
  const answer = (it: Json, ok: boolean) => setRes({ ...res, [it.item_id]: { ok, aided: repair ? !!(finalPhase[it.item_id] && shown[`final-${it.item_id}`]) : !!shown[it.item_id] } });
  const finish = async () => {
    try {
      const r = await api<Json>(`/tasks/${slot}/complete`, { body: { results: data.items.map((it: Json) => ({ item_id: it.item_id, ...(res[it.item_id] ?? { ok: false, aided: false }) })) } });
      setDone(r);
    } catch (e) { setMsg((e as ApiError).detail); }
  };
  const next = async () => {
    try {
      const home = await api<Json>("/home");
      const plan = home.plan;
      if (!plan) { router.push("/"); return; }
      const n = await api<Json>(`/plans/${plan.id}/next${localStorage.getItem("lq:onMove") === "1" ? "?move=true" : ""}`);
      if (!n.slot_id) { router.push("/"); return; }
      router.push(n.kind === "unit" ? `/play?unit=${n.unit_id}&slot=${n.slot_id}` : `/task?slot=${n.slot_id}`);
    } catch { router.push("/"); }
  };
  if (done) return (
    <section className="panel pop" aria-labelledby="t-done">
      <h2 id="t-done">{repair ? "修復を実施しました" : "後日の確認を実施しました"}</h2>
      <p>{repair ? `その場でできた ${done.ok} 件・まだ ${done.not_yet + done.aided} 件。` : `補助なしでできた ${done.ok} 件・英文を見てできた ${done.aided} 件・まだ ${done.not_yet} 件。`}</p>
      <p className="note">{repair ? "同じ文を後日、英文なしでもう一度確かめます（今日の予定とは別の日に入ります）。" : "できなかった文は、別の日にもう一度入ります。"}この結果は初見の成績・難度帯・XPには入りません。</p>
      <button className="primary big wide" onClick={next}>次へ進む</button>
    </section>
  );
  const allAnswered = data.items.every((it: Json) => res[it.item_id]);
  return (
    <section className="panel" aria-labelledby="t-h">
      <h2 id="t-h">{repair ? "短い修復" : "後日の確認"} <small>{data.items.length} 件</small></h2>
      <p className="note">{data.reason_text}</p>
      {msg && <Msg>{msg}</Msg>}
      {data.items.map((it: Json) => (
        <div className="qbox" key={it.item_id}>
          <p className="faint">{it.title}{it.kind === "phrase" ? `・語句「${it.term}」` : ""}</p>
          {repair && <p className="note">手順: {it.steps_ja}</p>}
          <div className="row">
            {active === it.item_id && player.state === "playing" ? <button onClick={player.pause}>❚❚ 止める</button> : <button onClick={() => play(it)}>▶ 原音</button>}
            {repair && !finalPhase[it.item_id] && <><button className="small" onClick={() => play(it, 0.8)}>▶ ゆっくり（0.8倍）</button><button className="small" onClick={() => play(it, 1, true)}>▶ くり返し</button></>}
          </div>
          {repair && !finalPhase[it.item_id] && (
            <>
              {!shown[it.item_id] ? <button className="small ghost" onClick={() => reveal(it.item_id)}>必要なら英文を見る</button>
                : <div className="evidence"><p className="en">{it.text}</p>{it.ja && <p className="faint">{it.ja}</p>}{it.glossary.map((g: Json) => <p key={g.term} className="faint"><span className="en">{g.term}</span>：{g.meaning_ja}</p>)}</div>}
              <button className="small primary" onClick={() => { setFinalPhase({ ...finalPhase, [it.item_id]: true }); player.pause(); }}>最後の確認へ（通常速度・英文なし）</button>
            </>
          )}
          {(!repair || finalPhase[it.item_id]) && !res[it.item_id] && (
            <>
              <p>{repair ? "通常速度で、英文を見ずに聞いて、意味が分かりましたか？" : it.kind === "phrase" ? `音を聞いて、「${it.term}」の意味を思い出せますか？（答える前に英文を見ると補助付きになります）` : "英文を見ずに聞いて、意味が分かりますか？（答える前に英文を見ると補助付きになります）"}</p>
              <div className="row" role="group" aria-label="確認の結果">
                <button onClick={() => answer(it, true)}>分かった</button>
                <button onClick={() => answer(it, false)}>まだ</button>
                {!repair && !shown[it.item_id] && <button className="small ghost" onClick={() => reveal(it.item_id)}>英文と訳を見る</button>}
              </div>
            </>
          )}
          {(res[it.item_id] || (!repair && shown[it.item_id])) && <div className="evidence"><p className="en">{it.text}</p>{it.ja && <p className="faint">{it.ja}</p>}{res[it.item_id] && <p className="faint">記録: {res[it.item_id].ok ? (res[it.item_id].aided ? "英文を見て分かった（補助付き）" : "補助なしで分かった") : "まだ"}</p>}</div>}
        </div>
      ))}
      <div className="sticky-actions row actions">
        <button className="primary big wide" onClick={finish} disabled={!allAnswered}>この課題を終える</button>
        <Link className="btn ghost" href="/">ホームへ</Link>
      </div>
    </section>
  );
}
