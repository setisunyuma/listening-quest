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
  const play = async (l: Json, rate = 1) => { setActive(key(l)); await player.play((await playableUrl(app.me.user_id, l.audio_url)).src, rate, 0); };
  const clear = async (l: Json) => {
    try { await api(`/units/${l.unit_id}/missed`, { body: { utterance_id: l.utterance_id, missed: false } }); reload(); }
    catch (e) { setMsg((e as ApiError).detail); }
  };
  if (!data) return error ? <Msg>{error}</Msg> : <p className="note" role="status">読み込み中…</p>;
  return (
    <section className="panel" aria-labelledby="missed-h">
      <h2 id="missed-h">聞き取れなかった文 <small>{data.lines.length} 文</small></h2>
      <p className="note">解説画面で「聞き取れなかった」を付けた文です。まず英文を見ずに聞き、次に英文を表示して確かめ、聞き取れたら「聞き取れた」で一覧から外します。</p>
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
            <button className="small ghost" onClick={() => clear(l)}>聞き取れた</button>
          </div>
          {shown.has(key(l)) && <p className="en">{l.text}</p>}
          {active === key(l) && player.state === "error" && <small role="alert">{player.error}</small>}
        </div>
      ))}
    </section>
  );
}
