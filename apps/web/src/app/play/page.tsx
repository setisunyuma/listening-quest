"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useApp } from "@/components/Shell";
import { BandChip, chime, DemoChip, Figure, Msg } from "@/components/ui";
import { api, ApiError, SKILL_LABEL, uid, type Ack, type Answer, type Attempt, type Json, type LearnerUnit, type Plan, type Result } from "@/lib/api";
import { getPack, kvDel, kvGet, kvSet, playableUrl, putPending, type Pack, type PlayEvent } from "@/lib/offline";
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
  const [confidence, setConfidence] = useState<Record<string, "sure" | "unsure" | "guess" | undefined>>({});  // L02: optional
  const [transcript, setTranscript] = useState<Json[] | null>(null);
  const [hints, setHints] = useState<Record<string, string>>({});
  const [active, setActive] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const started = useRef(new Date().toISOString());
  const pendingPlay = useRef<string | null>(null);
  const usedRates = useRef<Record<string, number>>({});
  // R03: the answer draft and every playback event live on the device until the server confirms the submission
  const draftKey = attempt ? `draft:${attempt.attempt_id}` : `draft:pack:${pack!.slot_id}`;
  const playEvents = useRef<PlayEvent[]>([]);
  const [draftReady, setDraftReady] = useState(false);
  const [restored, setRestored] = useState(false);
  useEffect(() => {
    kvGet<Json>(app.me.user_id, draftKey).then((d) => {
      if (d) {
        if (d.answers && Object.keys(d.answers).length) { setAnswers(d.answers); setRestored(true); }
        playEvents.current = d.play_events ?? [];
        if (d.started) started.current = d.started;
        if (offline && d.plays) setPlays(d.plays);
        if (d.rates) usedRates.current = d.rates;
      }
    }).catch(() => {}).finally(() => setDraftReady(true));
  }, [draftKey, app.me.user_id, offline]);
  const saveDraft = (patch: Json = {}) => {
    if (!draftReady) return;
    kvSet(app.me.user_id, draftKey, { answers, plays, rates: usedRates.current, play_events: playEvents.current, started: started.current, ...patch }).catch(() => {});
  };
  useEffect(() => { saveDraft(); }, [answers, plays, draftReady]);

  const player = usePlayer({
    onStarted: () => {
      const seg = pendingPlay.current;
      if (!seg) return; // resume after pause is not a new play
      pendingPlay.current = null;
      if (rate !== 1) usedRates.current[seg] = rate;
      // a play counts only once the audio really started (this callback); it is stored before it is sent
      const ev = { event_id: uid(), segment_id: seg, rate };
      playEvents.current = [...playEvents.current, ev];
      if (offline) setPlays((p) => ({ ...p, [seg]: (p[seg] ?? 0) + 1 }));
      else {
        saveDraft();
        api<Json>(`/attempts/${attempt!.attempt_id}/plays`, { body: ev }).then((r) => { setPlays(r.plays); setAids(r.aids); }).catch(() => setPlays((p) => ({ ...p, [seg]: (p[seg] ?? 0) + 1 })));
      }
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
  // practice tasks (統合指示書): purpose first, staged questions, permitted checks recorded apart
  const pr = u.practice as Json | null | undefined;
  const itemOf = (qid: string): Json | undefined => pr?.items.find((it: Json) => it.question_id === qid);
  const segIdx: Record<string, number> = Object.fromEntries(u.segments.map((sg, i) => [sg.id, i]));
  const heard = (x: Json) => !pr || (x.segment_ids ?? []).every((sid: string) => (plays[sid] ?? 0) > 0);
  const lockedInitial = (x: Json) => !!pr && itemOf(x.id)?.stage === "initial" && u.segments.some((sg) => !(x.segment_ids ?? []).includes(sg.id) && (plays[sg.id] ?? 0) > 0);
  const segOpen = (i: number) => !pr || u.questions.every((x) => {
    const ids: string[] = x.segment_ids ?? [];
    return !ids.length || ids.some((sid) => segIdx[sid] >= i) || answers[x.id] !== undefined;
  });
  const [checks, setChecks] = useState<Json[]>([]);
  const clarify = async (cid: string) => {
    try {
      const r = await api<Json>(`/attempts/${attempt!.attempt_id}/clarify`, { body: { event_id: uid(), clarification_id: cid, initial_answers: Object.fromEntries(u.questions.map((x) => [x.id, answers[x.id] ?? null])) } });
      setChecks((c) => [...c.filter((x) => x.id !== r.id), r]);
    } catch (e) { setMsg((e as ApiError).detail); }
  };
  const ready = u.questions.every((x) => answers[x.id] !== undefined);
  const submit = async () => {
    setBusy(true); setMsg(""); player.pause();
    const body = Object.fromEntries(u.questions.map((x) => [x.id, answers[x.id] ?? null]));
    if (offline) {
      try {
        await putPending(app.me.user_id, { event_id: uid(), unit_id: pack!.unit_id, unit_revision: pack!.unit_revision, answers: body, plays, rates: usedRates.current, plan_id: pack!.plan_id, slot_id: pack!.slot_id,
          assignment_version: pack!.assignment_version, client_started_at: started.current, client_answered_at: new Date().toISOString(), title: pack!.card.part_label });
      } catch { setMsg("端末に保存できませんでした。回答はまだ保存されていません。"); setBusy(false); return; }
      await kvDel(app.me.user_id, draftKey).catch(() => {});
      await app.refreshPending();
      setSaved(true); setBusy(false);
      app.syncNow();
      return;
    }
    try {
      // every playback event goes with the answers: a replay or speed change that did not reach the server
      // earlier is applied before grading, so an assisted attempt can never be graded as unassisted
      onResult(await api<Result>(`/attempts/${attempt!.attempt_id}/submit`, { body: { answers: body, plays: playEvents.current, confidence: Object.fromEntries(Object.entries(confidence).filter(([, v]) => v)) } }));
      kvDel(app.me.user_id, draftKey).catch(() => {});
    } catch (e) {
      const er = e as ApiError;
      if (er.offline) {
        try {
          await putPending(app.me.user_id, { event_id: uid(), attempt_id: attempt!.attempt_id, play_events: playEvents.current, unit_id: attempt!.unit_id, unit_revision: attempt!.unit_revision,
            answers: body, plays, rates: usedRates.current, client_started_at: started.current, client_answered_at: new Date().toISOString(), title: attempt!.unit.part });
          await app.refreshPending();
          setSaved(true);
        } catch { setMsg("通信できず、端末への保存にも失敗しました。回答は画面に残っています。もう一度「解答する」を押してください。"); }
      } else setMsg(er.detail);
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
          {u.presentation_profile.kind === "audio_arranged" && <span className="chip">音声向けアレンジ</span>}
          {pr && <span className="chip cat-practice">実践課題（試験形式の成績とは別に記録）</span>}</div>
        {pr && (
          <div className="msg ok" style={{ marginTop: 10 }} data-testid="practice-purpose">
            <p><strong>目的:</strong> {pr.purpose_ja}</p>
            {pr.listener_ja && <p><strong>伝える相手:</strong> {pr.listener_ja}</p>}
            <p><strong>できたらよいこと:</strong> {pr.success_ja}</p>
            {pr.generated && <p className="faint">生成した練習用の場面です（実録音ではありません）。</p>}
          </div>
        )}
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
                    : <button className="playbtn wide primary" onClick={() => startPlay(seg)} disabled={st === "loading" || !segOpen(i)}>
                        {st === "loading" ? "読み込み中…" : n === 0 ? `▶ ${u.segments.length > 1 ? `音声${i + 1}を` : ""}再生` : n < seg.plays ? `▶ ${n + 1}回目を再生` : "↻ もう一度聞く（補助として記録）"}</button>}
                </div>
                <p className="faint" aria-live="polite">規定 {seg.plays} 回・再生済み {n} 回{st === "playing" ? "・再生中" : st === "ended" ? "・再生終了" : ""}{u.questions.some((x) => x.prompt_medium === "audio") && i === u.segments.length - 1 ? "・問いは本文の後に音声で流れます" : ""}</p>
                {isActive && st === "error" && <Msg>{player.error} <button className="small" onClick={() => startPlay(seg)}>再試行</button></Msg>}
              </div>
            );
          })}
        </div>
        {restored && <Msg kind="ok">前回の途中の回答を端末から復元しました。</Msg>}
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
        {pr && u.segments.length > 1 && <p className="note">前半を聞いて暫定の判断を答えてから、後半（更新情報）を聞きます。暫定の答えは後から書き換えず、両方を記録します。</p>}
        {u.questions.filter(heard).map((x) => (
          <div className="qbox" key={x.id}>
            <h3>問 {x.slot}{u.questions.length > 1 ? ` / ${u.questions.length}` : ""}{itemOf(x.id) && <small> {itemOf(x.id)!.label_ja}{itemOf(x.id)!.stage === "initial" ? "（暫定）" : ""}</small>}</h3>
            {x.prompt_en ? <p className="en">{x.prompt_en}</p> : <p className="note">問いは音声で流れます。</p>}
            {hints[x.id] && <p className="msg warn">ヒント: {hints[x.id]}</p>}
            <div className="choices" role="group" aria-label={`問${x.slot}の選択肢`}>
              {x.choices.map((c, i) => (
                <button key={c.id} className="choice" aria-pressed={answers[x.id]?.choice_id === c.id} disabled={lockedInitial(x)} onClick={() => setAnswers({ ...answers, [x.id]: { choice_id: c.id } })}>
                  <span className="num" aria-hidden="true">{i + 1}</span><span className="en">{c.text}</span>
                </button>
              ))}
            </div>
            {!offline && answers[x.id] && (
              <div className="row" role="group" aria-label={`問${x.slot}の確信度（任意）`}>
                <small>確信度（任意）:</small>
                {([["sure", "根拠あり"], ["unsure", "迷った"], ["guess", "勘"]] as const).map(([k, label]) => (
                  <button key={k} className="small ghost" aria-pressed={confidence[x.id] === k} onClick={() => setConfidence({ ...confidence, [x.id]: confidence[x.id] === k ? undefined : k })}>{label}</button>
                ))}
              </div>
            )}
            <button className="ghost small" aria-pressed={answers[x.id] === null} onClick={() => setAnswers({ ...answers, [x.id]: answers[x.id] === null ? undefined : null })}>{answers[x.id] === null ? "✓ スキップする（取り消す）" : "この問題をスキップ"}</button>
          </div>
        ))}
        {pr && !offline && pr.clarifications.length > 0 && (
          <div className="qbox" aria-label="確認する">
            <h3>確認する <small>（場面の中で許される確認・最大 {pr.max_clarifications} 回）</small></h3>
            <p className="note">確認する前の答えが「最初の理解」、確認した後の答えが「確認後の達成」として別々に記録されます。確認は失敗扱いになりません。</p>
            {pr.clarifications.map((c: Json) => {
              const got = checks.find((x) => x.id === c.id);
              return (
                <div key={c.id}>
                  <button className="small" onClick={() => clarify(c.id)} disabled={!!got || checks.length >= pr.max_clarifications}>{c.label_ja}</button>
                  {got && <p className="en msg ok">{got.reply_en}</p>}
                </div>
              );
            })}
          </div>
        )}
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
      const open = (x: Json) => x.state === "pending" || x.state === "started";  // skipped / carried tasks are not "next"
      const s = plan?.slots.find((x) => open(x) && x.unit && x.id !== result.slot_id) ?? plan?.slots.find((x) => open(x) && x.unit);
      const href = (x: Json) => (x.kind === "repair" || x.kind === "recall" ? `/task?slot=${x.id}` : `/play?unit=${x.unit!.id}&slot=${x.id}`);
      router.push(s && plan?.kind === "quest" && s.id !== result.slot_id ? href(s) : "/");
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
  const [chosen, setChosen] = useState<Record<string, boolean>>({});
  const choosePhrase = async (term: string) => {
    const line = result.transcript.flatMap((sg: Json) => sg.lines).find((l: Json) => l.text.toLowerCase().includes(term.toLowerCase()));
    if (!line) return;
    const on = !chosen[term];
    setChosen({ ...chosen, [term]: on });
    try { await api(`/units/${result.unit_id}/phrases`, { body: { utterance_id: line.utterance_id, term, on } }); } catch (e) { setChosen({ ...chosen, [term]: !on }); setMsg((e as ApiError).detail); }
  };
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
        {result.notes_audit && ["unaudited", "structure_ok", "needs_check"].includes(result.notes_audit.state) && (
          <p className="faint">{result.notes_audit.state === "needs_check" ? "和訳・語注に要確認の箇所があります（否定の対応など）。" : "和訳・語注は自動の構造確認のみで、意味の監査は未実施です。"}</p>
        )}
        {result.feedback_audio && result.ja_audio_state === "unverified" && <p className="faint">日本語の解説音声は自動照合していません（英語の音声は照合済み）。</p>}
        {result.feedback_audio && (
          <div className="row">
            {active === "fb" && player.state === "playing" ? <button onClick={player.pause}>❚❚ 解説音声を止める</button> : <button onClick={() => playSrc("fb", result.feedback_audio!.url)}>▶ 正答と解説を音声で聞く</button>}
            {active === "fb" && player.state === "error" && <small role="alert">{player.error}</small>}
          </div>
        )}
      </section>
      {result.practice && <PracticeResult p={result.practice} />}
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
            {result.glossary.map((g: Json) => <p key={g.term} className="row"><span style={{ flex: 1 }}><strong className="en">{g.term}</strong>　{g.meaning_ja}</span>
              <button className="small ghost" aria-pressed={!!chosen[g.term]} onClick={() => choosePhrase(g.term)}>{chosen[g.term] ? "✓ 覚える" : "覚える"}</button></p>)}
            <p className="faint">「覚える」を付けた語句だけ、翌日以降の「今日やること」で、音から意味を思い出す確認に入ります。</p>
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
                {ack.repair_slot_id ? <Link className="btn primary big" href={`/task?slot=${ack.repair_slot_id}`}>短い修復へ進む</Link>
                  : <button className="primary big" onClick={next}>{result.slot_id && !ack.plan_completed ? "次へ進む" : "ホームへ"}</button>}
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

function PracticeResult({ p }: { p: Json }) {
  const items = Object.entries(p.items as Record<string, Json>);
  const pct = (v: number | null) => (v === null || v === undefined ? "判定保留" : `${Math.round(v * 100)}%`);
  const checked = p.clarifications.length > 0;
  return (
    <section className="panel" aria-labelledby="pr-h" data-testid="practice-result">
      <h2 id="pr-h">実践課題: {p.title_ja}</h2>
      <p className="note">{p.purpose_ja}</p>
      <dl className="kv">
        <dt>最初の理解（主要項目）</dt><dd>{pct(p.first_understanding)}</dd>
        {checked && <><dt>確認後の達成</dt><dd>{pct(p.task_success)}（確認 {p.clarifications.length} 回）</dd></>}
      </dl>
      <div className="table-wrap"><table className="fig">
        <thead><tr><th scope="col">項目</th><th scope="col">最初</th>{checked && <th scope="col">確認後</th>}</tr></thead>
        <tbody>{items.map(([qid, it]) => (
          <tr key={qid}><td>{p.rubric_labels[it.rubric] ?? it.rubric}{it.main ? "" : "（補助項目）"}</td>
            <td>{it.first.pending ? "判定保留" : it.first.correct ? "◯" : "×"}</td>
            {checked && <td>{it.final.pending ? "判定保留" : it.final.correct ? "◯" : "×"}</td>}</tr>
        ))}</tbody>
      </table></div>
      {p.critical_first.length > 0 && <Msg kind="warn">重大な取り違え: {p.critical_first.map((k: string) => p.critical_labels[k] ?? k).join("・")}。ほかの項目が正しくても、この点は短い確認で見直します。</Msg>}
      {p.clarification_replies.map((c: Json) => <p key={c.id} className="note">確認「{c.label_ja}」への返答: <span className="en">{c.reply_en}</span></p>)}
      <p className="faint">{p.evidence_eligible ? "次の課題に進むための証拠として記録しました。" : `証拠には数えません（${p.not_evidence_reasons.join("・")}）。練習としては記録されています。`} {p.generated_note_ja}</p>
      <p className="faint">英語の表現の良し悪しは聴解の判定に入れていません。級や CEFR の認定ではありません。</p>
    </section>
  );
}

function ReportBox({ unitId, attemptId }: { unitId: string; attemptId: string }) {
  const [kind, setKind] = useState("");
  const [note, setNote] = useState("");
  const [done, setDone] = useState("");
  const [err, setErr] = useState("");
  const send = async () => {
    try {
      const r = await api<Json>(`/units/${unitId}/reports`, { body: { kind, note, attempt_id: attemptId } });
      setDone(r.withdrawn ? "報告しました。この教材は新しい出題とラジオの候補から外しました。作成済みのラジオ番組では、この部分を飛ばして作り直しを案内します（保存済みの端末は次回の同期で反映。教材管理で元に戻せます）。" : "報告しました。教材管理の「報告」に記録されます。");
    } catch (e) { setErr((e as ApiError).detail); }
  };
  if (done) return <Msg kind="ok">{done}</Msg>;
  return (
    <details className="report"><summary className="btn ghost small">この教材を報告する</summary>
      <div className="row" role="group" aria-label="報告の種類">
        {REPORT_KINDS.map(([id, label]) => <button key={id} className="small" aria-pressed={kind === id} onClick={() => setKind(kind === id ? "" : id)}>{kind === id ? "✓ " : ""}{label}</button>)}
      </div>
      <label className="field">メモ（任意）<textarea value={note} maxLength={1000} rows={2} onChange={(e) => setNote(e.target.value)} placeholder="例: seed exchange が何か分からない" /></label>
      {(kind === "wrong" || kind === "audio") && <p className="faint">送ると、この教材は新しい出題とラジオの候補から外れ、作成済みのラジオ番組では飛ばされます。</p>}
      <button className="small primary" disabled={!kind} onClick={send}>報告を送る</button>
      {err && <Msg>{err}</Msg>}
    </details>
  );
}
