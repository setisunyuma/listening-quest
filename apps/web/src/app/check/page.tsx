"use client";
import { useEffect, useRef, useState } from "react";
import { useApp } from "@/components/Shell";
import { DemoChip, Figure, Msg, useData } from "@/components/ui";
import { api, ApiError, fmtClock, fmtMin, MODE_LABEL, type Answer, type Json } from "@/lib/api";
import { kvDel, kvGet, kvSet } from "@/lib/offline";
import { usePlayer } from "@/lib/usePlayer";

export default function CheckPage() {
  const app = useApp();
  const sets = useData<Json>(() => api(`/assessments/sets?mode=${app.mode}`), [app.mode]);
  const [paper, setPaper] = useState<Json | null>(null);
  const [result, setResult] = useState<Json | null>(null);
  const [msg, setMsg] = useState("");
  const open = sets.data?.sessions.find((s: Json) => s.status === "open");

  const start = async (id: string) => {
    setMsg("");
    try { setPaper(await api("/assessments", { body: { mock_set_id: id } })); }
    catch (e) { const er = e as ApiError; setMsg(er.offline ? "実力チェックはオンラインでのみ受けられます。" : er.status === 409 ? "このセットには既に接触した教材が含まれているため開始できません。教材管理で新しいセットを作成してください。" : er.detail); sets.reload(); }
  };
  if (result) return <ResultView result={result} onClose={() => { setResult(null); setPaper(null); sets.reload(); }} />;
  if (paper) return <Paper paper={paper} onDone={setResult} />;
  if (!sets.data) return sets.error ? <Msg>{sets.error}</Msg> : <p className="note" role="status">読み込み中…</p>;
  const label = sets.data.exam.label_ja;
  return (
    <>
      {msg && <Msg>{msg}</Msg>}
      <section className="panel accent"><h2>本番条件の{label} <small>{MODE_LABEL[app.mode]}</small></h2>
        <p className="note">{sets.data.profile_label}。未接触の教材だけを使い、開始時に全問題を固定します。途中で解説・英文表示・ヒント・追加再生はできません。音声は規定の回数だけ自動で流れます。</p>
        <p className="faint">{sets.data.exam.venue_note_ja}{sets.data.exam.kind === "app_check" ? "" : " 日次の学習計画とは別枠です。"}</p>
        {open && <div className="msg warn">受験中のセッションがあります。<button className="small" onClick={async () => setPaper(await api(`/assessments/${open.session_id}`))}>続きから</button></div>}
        {sets.data.sets.length === 0 ? <Msg kind="warn">開始できるセットがありません。「教材管理」で{label}セットを作成してください（全素材がそろうと作成できます）。</Msg> : sets.data.sets.map((s: Json) => (
          <div key={s.id} className="qbox"><p><strong>{s.label}</strong>：{s.slots}問・{s.units} Unit・所要時間 <strong>{fmtMin(s.duration_sec)}</strong>（完成音声の長さから算出{s.answer_time_min ? `／解答時間の規定 ${s.answer_time_min}分` : ""}）</p>
            <button className="primary big" onClick={() => { if (window.confirm(`${s.label}を開始します。所要 ${fmtMin(s.duration_sec)}。途中で中断しても音声は巻き戻せません。開始しますか？`)) start(s.id); }} disabled={!app.online}>開始する</button></div>
        ))}
      </section>
      <section className="panel"><h2>これまでの結果</h2>
        {sets.data.sessions.filter((s: Json) => s.status === "submitted").length === 0 ? <p className="note">まだ結果がありません。</p> : sets.data.sessions.filter((s: Json) => s.status === "submitted").map((s: Json) => (
          <p key={s.session_id} className="row"><span>{s.started_at.slice(0, 10)}　<Score r={s.result} /></span><button className="small" onClick={async () => setResult(await api(`/assessments/${s.session_id}/result`))}>解説を見る</button></p>))}
      </section>
    </>
  );
}

function Score({ r }: { r: Json }) {
  return <strong>{r.score_kind === "points" ? `${r.points} / ${r.max_points} 点` : `${MODE_LABEL[r.mode]} ${r.correct_count} / ${r.slot_count}`}</strong>;
}

function Paper({ paper, onDone }: { paper: Json; onDone: (r: Json) => void }) {
  const app = useApp();
  const [idx, setIdx] = useState(-1); // -1: not started
  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [resumed, setResumed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const idxRef = useRef(-1);
  const startedAt = useRef<number | null>(null);
  // R05: answers, position and start time live on the device, so a reload or PWA restart does not reset them
  const key = `draft:assessment:${paper.session_id}`;
  const condition = (kind: string, detail = "") => api(`/assessments/${paper.session_id}/conditions`, { body: { kind, detail } }).catch(() => {});
  useEffect(() => {
    kvGet<Json>(app.me.user_id, key).then((d) => {
      if (d && (d.idx >= 0 || Object.keys(d.answers ?? {}).length)) {
        setAnswers(d.answers ?? {}); idxRef.current = d.idx; setIdx(d.idx); startedAt.current = d.startedAt ?? null;
        setResumed(true);
        condition("resume", `item ${d.idx + 1}`); // interrupted: the result is labelled as conditional, not standard
      }
    }).catch(() => {}).finally(() => setLoaded(true));
  }, [key]);
  useEffect(() => { if (loaded) kvSet(app.me.user_id, key, { answers, idx, startedAt: startedAt.current }).catch(() => {}); }, [answers, idx, loaded]);
  const player = usePlayer({
    onEnded: () => { const n = idxRef.current + 1; if (n < paper.items.length) playItem(n); else { idxRef.current = paper.items.length; setIdx(paper.items.length); } },
  });
  const playItem = async (i: number, again = false) => {
    if (startedAt.current === null) startedAt.current = Date.now();
    if (again) condition("retry", `item ${i + 1}`);
    idxRef.current = i; setIdx(i); await player.play(paper.items[i].audio.url, 1, 0);
  };
  useEffect(() => {
    if (idx < 0) return;
    // elapsed time comes from the stored start, not a counter in the page (no reset to zero on reload)
    const tick = () => setElapsed(startedAt.current ? Math.floor((Date.now() - startedAt.current) / 1000) : 0);
    tick();
    const t = setInterval(tick, 1000); return () => clearInterval(t);
  }, [idx >= 0]);
  useEffect(() => { if (player.state === "error") condition("audio_error", `item ${idxRef.current + 1}`); }, [player.state]);
  const submit = async () => {
    const blank = paper.slot_count - Object.keys(answers).length;
    if (blank > 0 && !window.confirm(`未回答が ${blank} 問あります（0点として採点されます）。提出しますか？`)) return;
    setBusy(true); player.pause();
    try { await api(`/assessments/${paper.session_id}/submit`, { body: { answers } }); kvDel(app.me.user_id, key).catch(() => {}); onDone(await api(`/assessments/${paper.session_id}/result`)); }
    catch (e) { setMsg((e as ApiError).offline ? "通信できませんでした。回答は画面に残っています。もう一度提出してください。" : (e as ApiError).detail); }
    setBusy(false);
  };
  const finished = idx >= paper.items.length;
  return (
    <>
      <section className="panel accent" style={{ position: "sticky", top: 0, zIndex: 6 }}>
        <div className="row"><strong>{paper.label}</strong><span className="chip">{Object.keys(answers).length} / {paper.slot_count} 回答</span><span className="chip">経過 {fmtClock(elapsed * 1000)} / 目安 {fmtMin(paper.duration_sec)}</span><DemoChip kind={paper.items[0]?.audio_kind} />{resumed && <span className="chip demo">中断・再開あり（条件付き）</span>}</div>
        {resumed && idx >= 0 && idx < paper.items.length && player.state !== "playing" && <p className="note">前回の続きです（回答は端末から復元しました）。<button className="small" onClick={() => playItem(Math.max(0, idx), true)}>この音声から再開</button></p>}
        {idx < 0 ? <button className="primary big wide" onClick={() => playItem(0)}>音声を開始する</button>
          : finished ? <p className="note" role="status">音声は終了しました。見直して提出してください。</p>
          : <p className="note" role="status" aria-live="polite">{player.state === "playing" ? "♪ 再生中" : player.state === "loading" ? "読み込み中" : "待機中"}：{paper.items[idx].part_label}（{idx + 1} / {paper.items.length}）</p>}
        {player.state === "error" && <Msg>{player.error} <button className="small" onClick={() => playItem(Math.max(0, idx), true)}>この音声から再開</button></Msg>}
        {msg && <Msg>{msg}</Msg>}
      </section>
      {paper.items.map((it: Json, i: number) => (
        <section className="panel" key={it.unit_id} aria-current={i === idx ? "step" : undefined} style={i === idx ? { borderColor: "var(--cyan)" } : undefined}>
          <h3 style={{ marginTop: 0 }}>{it.part_label}</h3>
          {it.unit.pre_info?.situation_en && <p className="en"><strong>Situation:</strong> {it.unit.pre_info.situation_en}</p>}
          {it.unit.figures.map((f: Json) => <Figure key={f.id} fig={f} />)}
          {it.unit.questions.map((q: Json) => (
            <div className="qbox" key={q.id}>
              <p><strong>解答欄 {q.global_slot}</strong> {q.prompt_en ? <span className="en">{q.prompt_en}</span> : <small>（問いは音声で流れます）</small>}</p>
              <div className="choices" role="group" aria-label={`解答欄${q.global_slot}`}>
                {q.choices.map((c: Json, k: number) => (<button key={c.id} className="choice" aria-pressed={answers[String(q.global_slot)]?.choice_id === c.id} onClick={() => setAnswers({ ...answers, [String(q.global_slot)]: { choice_id: c.id } })}><span className="num" aria-hidden="true">{k + 1}</span><span className="en">{c.text}</span></button>))}
              </div>
            </div>
          ))}
        </section>
      ))}
      <div className="sticky-actions"><button className="primary big wide" onClick={submit} disabled={busy || idx < 0}>{busy ? "提出中…" : "提出して採点する"}</button></div>
    </>
  );
}

function ResultView({ result, onClose }: { result: Json; onClose: () => void }) {
  const r = result.result;
  return (
    <>
      <section className="panel accent pop"><h2>{r.label}の結果</h2>
        <p style={{ fontSize: "1.6rem" }}><Score r={r} /></p>
        {r.standard === false && <p className="chip demo">条件付きの結果（{(r.conditions as string[]).map((c) => ({ resume: "中断・再開", replay: "聞き直し", audio_error: "音声エラー", retry: "再試行" } as Record<string, string>)[c] ?? c).join("・")}あり）: 標準条件の結果とは区別します</p>}
        <div className="table-wrap"><table className="list"><thead><tr><th scope="col">区分</th><th scope="col">正答数</th>{r.score_kind === "points" && <th scope="col">得点</th>}</tr></thead>
          <tbody>{Object.entries(r.by_section as Record<string, Json>).map(([k, v]) => <tr key={k}><th scope="row">{r.mode === "common_test" ? `第${k}問` : k}</th><td>{v.correct} / {v.total}</td>{r.score_kind === "points" && <td>{v.points} / {v.max_points}</td>}</tr>)}</tbody></table></div>
        <p className="faint">{r.kind === "app_check" ? "アプリ独自の構成による確認です。CEFRの認定や公式試験の結果を示すものではありません。" : r.score_kind === "count" ? "正答数のみを表示します。CSEスコアや合否への換算は行いません。" : "形式を参考にしたオリジナル問題での得点です。本試験の得点を保証するものではありません。"} 未回答は0点です。</p>
        <button onClick={onClose}>閉じる</button>
      </section>
      {result.units.map((u: Json) => (
        <section className="panel" key={u.unit_id}>
          {u.questions.map((q: Json) => (
            <div className="qbox" key={q.id}>
              <h3 style={{ marginTop: 0 }}>解答欄 {q.global_slot} <span className={`verdict ${q.correct ? "ok" : q.skipped ? "skip" : "ng"}`}>{q.correct ? "○ 正解" : q.skipped ? "－ 未回答" : "× 不正解"}</span></h3>
              <p className="en">{q.prompt_en}</p>
              <p className="en"><strong>Answer:</strong> {q.answer_en}{q.your_answer && !q.correct && <small>　あなたの解答: {q.choices.find((c: Json) => c.id === q.your_answer.choice_id)?.text}</small>}</p>
              {q.evidence.filter((e: Json) => e.type === "utterance").map((e: Json) => <p key={e.utterance_id} className="evidence en"><strong>{e.speaker}:</strong> {e.text}</p>)}
              <p><strong>要点:</strong> {q.explanation_ja}</p>
            </div>
          ))}
        </section>
      ))}
    </>
  );
}
