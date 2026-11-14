"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useApp } from "@/components/Shell";
import { BandChip, chime, DemoChip, Figure, Msg } from "@/components/ui";
import { api, ApiError, SKILL_LABEL, uid, type Ack, type Answer, type Attempt, type Json, type LearnerUnit, type Plan, type Result } from "@/lib/api";
import { getPack, playableUrl, putPending, type Pack } from "@/lib/offline";
import { usePlayer } from "@/lib/usePlayer";

const REASONS = [["sound", "音が取れない"], ["vocabulary", "語彙"], ["numbers_negation", "数字・否定"], ["inference", "推論"], ["organizing", "情報整理"], ["focus", "集中"], ["other", "その他"]];
const NOT_FIRST: Record<string, string> = {
  prior_exposure: "以前に接触した教材", not_first_attempt: "初回の解答ではない", extra_replay: "規定回数を超えて再生", speed_changed: "再生速度を変更", transcript: "英文を表示", hint: "ヒントを表示",
  prompt_text: "問いを文字で表示", answer_access_elsewhere: "別の経路で解答に接触", incomplete: "未回答あり", "session_type:review": "復習として解答",
};

export default function PlayPage() {
  return <Suspense fallback={<p className="note">読み込み中…</p>}><Play /></Suspense>;
}

function Play() {
  const q = useSearchParams();
  const unitId = q.get("unit"), slotId = q.get("slot"), attemptParam = q.get("attempt");
  const app = useApp();
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [pack, setPack] = useState<Pack | null>(null);
  const [error, setError] = useState("");
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let alive = true;
    (async () => {
      setError(""); setAttempt(null); setResult(null); setPack(null);
      try {
        if (attemptParam) { const r = await api<Result>(`/attempts/${attemptParam}/result`); if (alive) setResult(r); return; }
        if (!unitId) { setError("教材が指定されていません。"); return; }
        const key = `lq:att:${unitId}:${slotId ?? ""}`;
        const id = sessionStorage.getItem(key) || uid();
        const a = await api<Attempt>("/attempts", { body: { attempt_id: id, unit_id: unitId, slot_id: slotId } });
        sessionStorage.setItem(key, a.attempt_id);
        if (!alive) return;
        if (a.status === "submitted") setResult(await api<Result>(`/attempts/${a.attempt_id}/result`));
        else setAttempt(a);
      } catch (e) {
        const er = e as ApiError;
        if (er.offline && slotId) {
          const p = await getPack(app.me.user_id, slotId);
          if (alive && p) { setPack(p); return; }
        }
        if (alive) setError(er.offline ? "オフラインです。この問題は端末に保存されていません。" : er.status === 409 ? `${er.detail}（ホームに戻って最新の予定を確認してください）` : er.detail);
      }
    })();
    return () => { alive = false; };
  }, [unitId, slotId, attemptParam, nonce, app.me.user_id]);

  if (error) return <><Msg>{error}</Msg><Link className="btn" href="/">ホームへ戻る</Link></>;
  if (result) return <ResultView result={result} onRetry={() => { sessionStorage.removeItem(`lq:att:${unitId}:${slotId ?? ""}`); setNonce((n) => n + 1); }} canRetry={!attemptParam} />;
  if (attempt) return <AnswerView key={attempt.attempt_id} unit={attempt.unit} attempt={attempt} onResult={setResult} />;
  if (pack) return <AnswerView key={pack.slot_id} unit={pack.unit} pack={pack} onResult={() => {}} />;
  return <p className="note" role="status">読み込み中…</p>;
}

function AnswerView({ unit, attempt, pack, onResult }: { unit: LearnerUnit; attempt?: Attempt; pack?: Pack; onResult: (r: Result) => void }) {
  const app = useApp();
  const router = useRouter();
  const offline = !attempt;
  const [u, setU] = useState<LearnerUnit>(unit);
  const [plays, setPlays] = useState<Record<string, number>>(attempt?.plays ?? {});
  const [aids, setAids] = useState<Record<string, boolean>>(attempt?.aids ?? {});
  const [rate, setRate] = useState(1);
  const [answers, setAnswers] = useState<Record<string, Answer | undefined>>({});
  const [transcript, setTranscript] = useState<Json[] | null>(null);
  const [hints, setHints] = useState<Record<string, string>>({});
  const [active, setActive] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const started = useRef(new Date().toISOString());
  const pendingPlay = useRef<string | null>(null);
  const usedRates = useRef<Record<string, number>>({});

  const player = usePlayer({
    onStarted: () => {
      const seg = pendingPlay.current;
      if (!seg) return; // resume after pause is not a new play
      pendingPlay.current = null;
      if (rate !== 1) usedRates.current[seg] = rate;
      if (offline) setPlays((p) => ({ ...p, [seg]: (p[seg] ?? 0) + 1 }));
      else api<Json>(`/attempts/${attempt!.attempt_id}/plays`, { body: { event_id: uid(), segment_id: seg, rate } }).then((r) => { setPlays(r.plays); setAids(r.aids); }).catch(() => setPlays((p) => ({ ...p, [seg]: (p[seg] ?? 0) + 1 })));
    },
  });

  const startPlay = async (seg: Json) => {
    if (!seg.audio) { setMsg("この区間の音声がありません。"); return; }
    setMsg("");
    const { src } = await playableUrl(app.me.user_id, seg.audio.url);
    pendingPlay.current = seg.id;
    setActive(seg.id);
    const ok = await player.play(src, rate, 0);
    if (!ok) pendingPlay.current = null;
  };
  const aid = async (kind: "transcript" | "hint" | "prompt_text") => {
    try {
      const r = await api<Json>(`/attempts/${attempt!.attempt_id}/aids`, { body: { event_id: uid(), kind } });
      setAids(r.aids);
      if (kind === "transcript") setTranscript(r.segments);
      if (kind === "hint") setHints(r.hints);
      if (kind === "prompt_text") setU({ ...u, questions: u.questions.map((x) => ({ ...x, prompt_en: r.prompts[x.id] ?? x.prompt_en })) });
    } catch (e) { setMsg((e as ApiError).detail); }
  };
  const ready = u.questions.every((x) => answers[x.id] !== undefined);
  const submit = async () => {
    setBusy(true); setMsg(""); player.pause();
    const body = Object.fromEntries(u.questions.map((x) => [x.id, answers[x.id] ?? null]));
    if (offline) {
      await putPending(app.me.user_id, { event_id: uid(), unit_id: pack!.unit_id, unit_revision: pack!.unit_revision, answers: body, plays, rates: usedRates.current, plan_id: pack!.plan_id, slot_id: pack!.slot_id,
        assignment_version: pack!.assignment_version, client_started_at: started.current, client_answered_at: new Date().toISOString(), title: pack!.card.part_label });
      await app.refreshPending();
      setSaved(true); setBusy(false);
      app.syncNow();
      return;
    }
    try {
      onResult(await api<Result>(`/attempts/${attempt!.attempt_id}/submit`, { body: { answers: body } }));
    } catch (e) {
      const er = e as ApiError;
      setMsg(er.offline ? "通信できませんでした。回答は画面に残っています。電波の良い場所でもう一度「解答する」を押してください。" : er.detail);
    }
    setBusy(false);
  };

  if (saved) return (
    <section className="panel pop"><h2>解答済み・採点待ち</h2>
      <p>回答を端末に保存しました。オンラインに戻ると自動で送信され、採点・解説・次の問題の調整が行われます。</p>
      <p className="note">まだ採点されていません。正誤はここでは表示されません。</p>
      <button className="primary" onClick={() => router.push("/")}>ホームへ</button></section>
  );
  const anyAudioPrompt = u.questions.some((x) => x.prompt_medium === "audio" && !x.prompt_en);
  const assisted = Object.values(aids).some(Boolean);
  return (
    <>
      <section className="panel">
        <div className="row"><span className="chip">{u.part}</span>{attempt && <BandChip band={attempt.band} />}<DemoChip kind={attempt?.audio_kind ?? pack?.card.audio_kind} />
          {offline ? <span className="chip">オフライン解答</span> : attempt!.session_type === "review" ? <span className="chip purple">復習</span> : attempt!.counts_as_first && !assisted ? <span className="chip">初見として記録</span> : <span className="chip">練習として記録</span>}
          {u.presentation_profile.kind !== "official" && <span className="chip">音声向けアレンジ</span>}</div>
        {u.pre_info?.situation_en && (<div style={{ marginTop: 10 }}><h3>Situation <small>（再生前に読んでください）</small></h3><p className="en">{u.pre_info.situation_en}</p></div>)}
        {u.figures.map((f: Json) => <Figure key={f.id} fig={f} />)}
        <div className="playbox" style={{ marginTop: 12 }}>
          {u.segments.map((seg, i) => {
            const n = plays[seg.id] ?? 0, isActive = active === seg.id, st = isActive ? player.state : "idle";
            return (
              <div key={seg.id}>
                <div className="row">
                  {st === "playing" ? <button className="playbtn wide" onClick={player.pause} aria-label="一時停止">❚❚ 一時停止</button>
                    : st === "paused" ? <button className="playbtn wide primary" onClick={() => player.play()} aria-label="続きを再生">▶ 続きを再生</button>
                    : <button className="playbtn wide primary" onClick={() => startPlay(seg)} disabled={st === "loading"}>
                        {st === "loading" ? "読み込み中…" : n === 0 ? `▶ ${u.segments.length > 1 ? `音声${i + 1}を` : ""}再生` : n < seg.plays ? `▶ ${n + 1}回目を再生` : "↻ もう一度聞く（補助として記録）"}</button>}
                </div>
                <p className="faint" aria-live="polite">規定 {seg.plays} 回・再生済み {n} 回{st === "playing" ? "・再生中" : st === "ended" ? "・再生終了" : ""}{u.questions.some((x) => x.prompt_medium === "audio") && i === u.segments.length - 1 ? "・問いは本文の後に音声で流れます" : ""}</p>
                {isActive && st === "error" && <Msg>{player.error} <button className="small" onClick={() => startPlay(seg)}>再試行</button></Msg>}
              </div>
            );
          })}
        </div>
        {msg && <Msg>{msg}</Msg>}
        {!offline && (
          <details style={{ marginTop: 8 }}>
            <summary className="btn ghost small">学習の補助を使う</summary>
            <p className="note">補助を使うと「補助付きの結果」として記録され、初見の成績には入りません。</p>
            <div className="row">
              <label className="field" style={{ margin: 0 }}>再生速度<select value={rate} onChange={(e) => setRate(Number(e.target.value))} aria-label="再生速度"><option value={0.75}>0.75倍（補助）</option><option value={1}>通常</option><option value={1.25}>1.25倍（補助）</option></select></label>
              <button className="small" onClick={() => aid("transcript")} disabled={!!transcript}>英文を表示</button>
              {u.questions.some((x) => x.has_hint) && <button className="small" onClick={() => aid("hint")} disabled={Object.keys(hints).length > 0}>ヒント</button>}
              {anyAudioPrompt && <button className="small" onClick={() => aid("prompt_text")}>問いを文字で表示</button>}
            </div>
          </details>
        )}
        {transcript && (<div className="evidence" aria-label="英文">{transcript.map((s: Json) => s.lines.map((l: Json) => <p key={l.utterance_id} className="en"><strong>{l.speaker}:</strong> {l.text}</p>))}</div>)}
      </section>

      <section className="panel" aria-label="設問">
        {u.questions.map((x) => (
          <div className="qbox" key={x.id}>
            <h3>問 {x.slot}{u.questions.length > 1 ? ` / ${u.questions.length}` : ""}</h3>
            {x.prompt_en ? <p className="en">{x.prompt_en}</p> : <p className="note">問いは音声で流れます。</p>}
            {hints[x.id] && <p className="msg warn">ヒント: {hints[x.id]}</p>}
            <div className="choices" role="group" aria-label={`問${x.slot}の選択肢`}>
              {x.choices.map((c, i) => (
                <button key={c.id} className="choice" aria-pressed={answers[x.id]?.choice_id === c.id} onClick={() => setAnswers({ ...answers, [x.id]: { choice_id: c.id } })}>
                  <span className="num" aria-hidden="true">{i + 1}</span><span className="en">{c.text}</span>
                </button>
              ))}
            </div>
            <button className="ghost small" aria-pressed={answers[x.id] === null} onClick={() => setAnswers({ ...answers, [x.id]: answers[x.id] === null ? undefined : null })}>{answers[x.id] === null ? "✓ スキップする（取り消す）" : "この問題をスキップ"}</button>
          </div>
        ))}
        <div className="sticky-actions row actions">
          <button className="primary big wide" onClick={submit} disabled={!ready || busy}>{busy ? "送信中…" : offline ? "解答を保存する（採点は同期後）" : "解答する"}</button>
          {!ready && <small>すべての問いに答えるか、スキップを選ぶと解答できます。</small>}
        </div>
      </section>
    </>
  );
}

function ResultView({ result, onRetry, canRetry }: { result: Result; onRetry: () => void; canRetry: boolean }) {
  const app = useApp();
  const router = useRouter();
  const [ack, setAck] = useState<Ack | null>(null);
  const [reasons, setReasons] = useState<Record<string, string | null>>(Object.fromEntries(result.questions.map((x) => [x.id, x.self_reason])));
  const [msg, setMsg] = useState("");
  const [active, setActive] = useState("");
  const player = usePlayer();
  const playSrc = async (key: string, url: string) => { setActive(key); await player.play((await playableUrl(app.me.user_id, url)).src, 1, 0); };
  const all = result.complete && result.correct_count === result.total;

  const next = useCallback(async () => {
    try {
      const home = await api<Json>("/home");
      const plan: Plan | null = [home.plan, home.carried_plan].find((p: Plan | null) => p?.slots.some((s) => s.id === result.slot_id)) ?? home.plan;
      const s = plan?.slots.find((x) => x.state !== "completed" && x.unit && x.id !== result.slot_id) ?? plan?.slots.find((x) => x.state !== "completed" && x.unit);
      router.push(s && plan?.kind === "quest" && s.id !== result.slot_id ? `/play?unit=${s.unit!.id}&slot=${s.id}` : "/");
    } catch { router.push("/"); }
  }, [result.slot_id, router]);
  const confirm = async () => {
    try {
      const a = await api<Ack>(`/attempts/${result.attempt_id}/ack`, { body: {} });
      setAck(a);
      if (a.awarded.length) chime(!!app.settings.sfx);
    } catch (e) { setMsg((e as ApiError).detail); }
  };
  const replace = async () => {
    try {
      const r = await api<Json>(`/slots/${result.slot_id}/replace`, { body: {} });
      if (r.replaced) router.push(`/play?unit=${r.unit_id}&slot=${r.slot_id}`); else setMsg("この枠に合う別の教材が今はありません。");
    } catch (e) { setMsg((e as ApiError).detail); }
  };
  const [missed, setMissed] = useState<Set<string>>(new Set(result.missed ?? []));
  const toggleMissed = async (uttId: string) => {
    const on = !missed.has(uttId);
    const next = new Set(missed); if (on) next.add(uttId); else next.delete(uttId);
    setMissed(next);
    try { await api(`/units/${result.unit_id}/missed`, { body: { utterance_id: uttId, missed: on } }); }
    catch (e) { setMissed(missed); setMsg((e as ApiError).detail); }
  };
  const [rate, setRate] = useState(1);
  const lineAudio: Record<string, string> = Object.fromEntries(result.questions.flatMap((q) => q.evidence.filter((e) => e.type === "utterance" && e.audio_url).map((e) => [e.utterance_id, e.audio_url])));
  const [loop, setLoop] = useState(false);
  const [hideEn, setHideEn] = useState(false);
  const [showJa, setShowJa] = useState(false);
  const [revealed, setRevealed] = useState<Set<string>>(new Set());
  const hasJa = result.transcript.some((sg: Json) => sg.lines.some((l: Json) => l.ja));
  const playLine = async (id: string, url: string) => {
    setActive(id);
    player.setLoop(loop);
    await player.play((await playableUrl(app.me.user_id, url)).src, rate, 0);
  };
  const MissBtn = ({ id }: { id: string }) => (
    <button className="small ghost" aria-pressed={missed.has(id)} onClick={() => toggleMissed(id)}>{missed.has(id) ? "✓ 聞き取れなかった" : "聞き取れなかった"}</button>
  );
  const setReason = async (qid: string, reason: string | null) => {
    setReasons({ ...reasons, [qid]: reason });
    api(`/attempts/${result.attempt_id}/error-reasons`, { body: { question_id: qid, reason } }).catch(() => {});
  };
  return (
    <>
      <section className={`panel accent ${all ? "pop" : ""}`} aria-labelledby="res-h">
        <h2 id="res-h">結果 <small>{result.title}</small></h2>
        <div className="row">
          <span className={`verdict ${all ? "ok" : "ng"}`}>{result.correct_count} / {result.total} 正解</span>
          {all && result.category === "first" && <span className="chip rainbow">PERFECT 初見全問正解</span>}
          {all && result.category === "review" && !result.assisted && <span className="chip purple">復習全問正解</span>}
          <DemoChip kind={result.audio_kind} />
        </div>
        <p className="note">{result.counted_as_first ? "初見の成績（規定条件内）として記録しました。" : `記録の種類: ${{ review: "復習", assisted: "補助付き", repeat: "既出の練習", partial: "一部未回答", assessment: "実力チェック" }[result.category ?? ""] ?? "練習"}${result.not_first_reasons.length ? `（${result.not_first_reasons.map((r) => NOT_FIRST[r] ?? r).join("・")}）` : ""}`}</p>
        {result.schedule && <p className="note">次の復習: {result.schedule.due_day}（{result.schedule.reason_ja}）</p>}
        {result.band?.changed && <Msg kind="ok">このモードの難度帯が {result.band.band} になりました（直近の初見結果 {result.band.n} Unit の平均による調整）。</Msg>}
        {result.feedback_audio && (
          <div className="row">
            {active === "fb" && player.state === "playing" ? <button onClick={player.pause}>❚❚ 解説音声を止める</button> : <button onClick={() => playSrc("fb", result.feedback_audio!.url)}>▶ 正答と解説を音声で聞く</button>}
            {active === "fb" && player.state === "error" && <small role="alert">{player.error}</small>}
          </div>
        )}
      </section>
      {result.figures.length > 0 && <section className="panel">{result.figures.map((f: Json) => <Figure key={f.id} fig={f} highlight={result.questions.flatMap((x) => x.evidence.filter((e) => e.type === "figure").map((e) => e.element_id))} />)}</section>}
      <section className="panel" aria-label="設問ごとの解説">
        {result.questions.map((x) => {
          const mine = x.your_answer?.choice_id, right = x.correct_answer.choice_id;
          return (
            <div className="qbox" key={x.id}>
              <h3>問 {x.slot} <span className={`verdict ${x.correct ? "ok" : x.skipped ? "skip" : "ng"}`}>{x.correct ? "○ 正解" : x.skipped ? "－ スキップ" : "× 不正解"}</span></h3>
              <p className="en">{x.prompt_en}</p>
              <div className="choices">
                {x.choices.map((c, i) => (
                  <div key={c.id} className={`choice btn ${c.id === right ? "is-correct" : c.id === mine ? "is-wrong" : ""}`} style={{ cursor: "default" }}>
                    <span className="num" aria-hidden="true">{i + 1}</span>
                    <span><span className="en">{c.text}</span>
                      {c.id === right && <strong> ✓ 正答</strong>}{c.id === mine && <strong>{c.id === right ? "（あなたの解答）" : " ✗ あなたの解答"}</strong>}
                      {c.id !== right && x.distractors[c.id] && <small><br />{x.distractors[c.id]}</small>}</span>
                  </div>
                ))}
              </div>
              <p className="en"><strong>Answer:</strong> {x.answer_en}</p>
              {x.evidence.filter((e) => e.type === "utterance").map((e) => (
                <div className="evidence" key={e.utterance_id}>
                  <p className="en"><strong>{e.speaker}:</strong> {e.text}</p>
                  <div className="row">
                    {e.audio_url && <button className="small" onClick={() => playSrc(e.utterance_id, e.audio_url)} aria-label="根拠の音声を再生">{active === e.utterance_id && player.state === "playing" ? "♪ 再生中" : "▶ 根拠の音声"}</button>}
                    <MissBtn id={e.utterance_id} />
                  </div>
                </div>
              ))}
              <p><strong>要点:</strong> {x.explanation_ja}</p>
              {!x.correct && (
                <div>
                  <p className="note">つまずいた理由（任意・自己申告）{x.system_guess_label && <small>　システムの推定: {x.system_guess_label}</small>}</p>
                  <div className="row" role="group" aria-label="誤答理由">
                    {REASONS.map(([id, label]) => <button key={id} className="small" aria-pressed={reasons[x.id] === id} onClick={() => setReason(x.id, reasons[x.id] === id ? null : id)}>{reasons[x.id] === id ? "✓ " : ""}{label}</button>)}
                  </div>
                </div>
              )}
              <p className="faint">技能: {x.skill_tags.map((s) => SKILL_LABEL[s] ?? s).join("・")}</p>
            </div>
          );
        })}
        {result.glossary.length > 0 && (
          <div className="qbox" aria-label="語注"><h3>語注</h3>
            {result.glossary.map((g: Json) => <p key={g.term}><strong className="en">{g.term}</strong>　{g.meaning_ja}</p>)}
          </div>
        )}
        <details><summary className="btn ghost small">英文全体を見る</summary>
          <div className="row" role="group" aria-label="英文の表示">
            <button className="small" aria-pressed={hideEn} onClick={() => { setHideEn(!hideEn); setRevealed(new Set()); }}>{hideEn ? "✓ " : ""}英文を隠す（タップで表示）</button>
            {hasJa && <button className="small" aria-pressed={showJa} onClick={() => setShowJa(!showJa)}>{showJa ? "✓ " : ""}和訳を表示</button>}
          </div>
          <div className="evidence">{result.transcript.map((s: Json) => s.lines.map((l: Json) => (
            <div key={l.utterance_id} className="line"><div style={{ flex: 1 }}>
              {hideEn && !revealed.has(l.utterance_id)
                ? <button className="small ghost wide" onClick={() => setRevealed(new Set([...revealed, l.utterance_id]))}><strong>{l.speaker}:</strong> （タップで英文を表示）</button>
                : <p className="en"><strong>{l.speaker}:</strong> {l.text}</p>}
              {showJa && l.ja && <p className="faint">{l.ja}</p>}</div>
              <div className="row">{(l.audio_url ?? lineAudio[l.utterance_id]) && <button className="small" aria-label="この文を再生" onClick={() => playLine(l.utterance_id, l.audio_url ?? lineAudio[l.utterance_id])}>{active === l.utterance_id && player.state === "playing" ? "♪" : "▶"}</button>}<MissBtn id={l.utterance_id} /></div></div>
          )))}</div>
          <div className="row" role="group" aria-label="1文再生の速さ">
            {[0.8, 1, 1.2].map((r) => <button key={r} className="small" aria-pressed={rate === r} onClick={() => setRate(r)}>{r}倍</button>)}
            <button className="small" aria-pressed={loop} onClick={() => { setLoop(!loop); player.setLoop(!loop); }}>{loop ? "✓ " : ""}くり返し</button>
            {player.state === "playing" && <button className="small" onClick={player.pause}>❚❚ 止める</button>}
          </div>
          <p className="faint">「聞き取れなかった」を付けた文は、メニューの「聞き取れなかった文」でまとめて聞き直せます。ラジオでも優先して流れます。</p></details>
        <ReportBox unitId={result.unit_id} attemptId={result.attempt_id} />
        {msg && <Msg>{msg}</Msg>}
        <div className="sticky-actions">
          {ack ? (
            <div className="pop">
              {ack.awarded.length > 0 ? <p className="verdict ok">+{ack.awarded.reduce((a, b) => a + b.amount, 0)} XP{ack.plan_completed ? "　今日の計画を完了！" : ""}</p>
                : <p className="note">{ack.needs_all_answers ? "未回答があるため、この枠は未完了です（全問に答えると完了になります）。" : "この教材の今日の基本XPは獲得済みです。"}</p>}
              <div className="row actions">
                <button className="primary big" onClick={next}>{result.slot_id && !ack.plan_completed ? "次へ進む" : "ホームへ"}</button>
                {ack.needs_all_answers && canRetry && <button onClick={onRetry}>もう一度解く</button>}
                {ack.needs_all_answers && result.slot_id && <button onClick={replace}>別の問題に替える</button>}
              </div>
            </div>
          ) : result.session_type === "assessment" ? <Link className="btn" href="/check">実力チェックへ戻る</Link>
            : <button className="primary big wide" onClick={confirm}>解説を確認した</button>}
        </div>
      </section>
    </>
  );
}


const REPORT_KINDS: [string, string][] = [["unclear", "意味・解説が分かりにくい"], ["wrong", "答え・解説がおかしい"], ["audio", "音声がおかしい"], ["other", "その他"]];

function ReportBox({ unitId, attemptId }: { unitId: string; attemptId: string }) {
  const [kind, setKind] = useState("");
  const [note, setNote] = useState("");
  const [done, setDone] = useState("");
  const [err, setErr] = useState("");
  const send = async () => {
    try {
      const r = await api<Json>(`/units/${unitId}/reports`, { body: { kind, note, attempt_id: attemptId } });
      setDone(r.withdrawn ? "報告しました。この教材は出題とラジオから外しました（教材管理で元に戻せます）。" : "報告しました。教材管理の「報告」に記録されます。");
    } catch (e) { setErr((e as ApiError).detail); }
  };
  if (done) return <Msg kind="ok">{done}</Msg>;
  return (
    <details className="report"><summary className="btn ghost small">この教材を報告する</summary>
      <div className="row" role="group" aria-label="報告の種類">
        {REPORT_KINDS.map(([id, label]) => <button key={id} className="small" aria-pressed={kind === id} onClick={() => setKind(kind === id ? "" : id)}>{kind === id ? "✓ " : ""}{label}</button>)}
      </div>
      <label className="field">メモ（任意）<textarea value={note} maxLength={1000} rows={2} onChange={(e) => setNote(e.target.value)} placeholder="例: seed exchange が何か分からない" /></label>
      {(kind === "wrong" || kind === "audio") && <p className="faint">送ると、この教材はすぐに出題とラジオから外れます。</p>}
      <button className="small primary" disabled={!kind} onClick={send}>報告を送る</button>
      {err && <Msg>{err}</Msg>}
    </details>
  );
}
