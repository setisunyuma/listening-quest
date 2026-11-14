"use client";
import { useState } from "react";
import { useApp } from "@/components/Shell";
import { Msg, useData } from "@/components/ui";
import { api, ApiError, MODE_LABEL, type Json } from "@/lib/api";
import { playableUrl } from "@/lib/offline";
import { usePlayer } from "@/lib/usePlayer";

export default function MissedPage() {
  const app = useApp();
  const [mode, setMode] = useState<"all" | "mode">("all");
  const { data, error, reload } = useData<Json>(() => api(`/missed${mode === "mode" ? `?mode=${app.mode}` : ""}`), [mode, app.mode]);
  const player = usePlayer();
  const [active, setActive] = useState("");
  const [msg, setMsg] = useState("");
  const [shown, setShown] = useState<Set<string>>(new Set());
  const key = (l: Json) => `${l.unit_id}:${l.utterance_id}`;
  const [dict, setDict] = useState("");
  const [typed, setTyped] = useState("");
  const [grade, setGrade] = useState<Json | null>(null);
  const check = async (l: Json) => {
    try { setGrade(await api(`/units/${l.unit_id}/dictation`, { body: { utterance_id: l.utterance_id, typed } })); }
    catch (e) { setMsg((e as ApiError).detail); }
  };
  const play = async (l: Json, rate = 1) => { setActive(key(l)); await player.play((await playableUrl(app.me.user_id, l.audio_url)).src, rate, 0); };
  const clear = async (l: Json) => {
    try { await api(`/units/${l.unit_id}/missed`, { body: { utterance_id: l.utterance_id, missed: false } }); reload(); }
    catch (e) { setMsg((e as ApiError).detail); }
  };
  if (!data) return error ? <Msg>{error}</Msg> : <p className="note" role="status">読み込み中…</p>;
  return (
    <section className="panel" aria-labelledby="missed-h">
      <h2 id="missed-h">聞き取れなかった文 <small>{data.lines.length} 文</small></h2>
      <p className="note">解説画面で「聞き取れなかった」を付けた文です。まず英文を見ずに聞き、「書き取る」で聞こえたとおりに入力して確かめ、聞き取れたら「聞き取れた」で一覧から外します。</p>
      <div className="row" role="group" aria-label="対象">
        <button className="small" aria-pressed={mode === "all"} onClick={() => setMode("all")}>すべてのモード</button>
        <button className="small" aria-pressed={mode === "mode"} onClick={() => setMode("mode")}>{MODE_LABEL[app.mode]}だけ</button>
      </div>
      {msg && <Msg>{msg}</Msg>}
      {data.lines.length === 0 ? <p className="note">まだありません。</p> : data.lines.map((l: Json) => (
        <div className="qbox" key={key(l)}>
          <p className="faint">{l.title}・{MODE_LABEL[l.mode]}・{l.speaker}</p>
          <div className="row">
            {l.audio_url ? (active === key(l) && player.state === "playing"
              ? <button onClick={player.pause}>❚❚ 止める</button>
              : <><button onClick={() => play(l)}>▶ 聞く</button><button className="small" onClick={() => play(l, 0.8)}>▶ ゆっくり（0.8倍）</button></>)
              : <small>この文の音声はありません。</small>}
            <button className="small" aria-pressed={shown.has(key(l))} onClick={() => { const n = new Set(shown); if (n.has(key(l))) n.delete(key(l)); else n.add(key(l)); setShown(n); }}>{shown.has(key(l)) ? "英文を隠す" : "英文を表示"}</button>
            <button className="small" aria-pressed={dict === key(l)} onClick={() => { setDict(dict === key(l) ? "" : key(l)); setTyped(""); setGrade(null); }}>書き取る</button>
            <button className="small ghost" onClick={() => clear(l)}>聞き取れた</button>
          </div>
          {dict === key(l) && (
            <div>
              <label className="field">聞こえたとおりに英語で入力<textarea rows={2} value={typed} autoCapitalize="off" autoCorrect="off" spellCheck={false} onChange={(e) => setTyped(e.target.value)} /></label>
              <button className="small primary" disabled={!typed.trim()} onClick={() => check(l)}>採点</button>
              {grade && (
                <div className="evidence" aria-live="polite">
                  <p><strong>{grade.correct_words} / {grade.total_words} 語</strong>（{Math.round(grade.score * 100)}%）</p>
                  <p className="en">{grade.ops.map((o: Json, i: number) => o.op === "equal" ? <span key={i}>{o.expected.join(" ")} </span>
                    : <span key={i}>{o.expected.length > 0 && <mark title="聞き取れなかった語">{o.expected.join(" ")}</mark>}{o.typed.length > 0 && <del title="入力した語">{o.typed.join(" ")}</del>} </span>)}</p>
                  <p className="faint">黄色は正しい語（入力にない、または違う語）、取り消し線はあなたの入力です。数字の書き方・大文字小文字・記号は区別しません。</p>
                </div>
              )}
            </div>
          )}
          {shown.has(key(l)) && <p className="en">{l.text}</p>}
          {active === key(l) && player.state === "error" && <small role="alert">{player.error}</small>}
        </div>
      ))}
    </section>
  );
}
