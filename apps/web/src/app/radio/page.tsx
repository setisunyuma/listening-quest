"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useApp } from "@/components/Shell";
import { DemoChip, Msg, useData } from "@/components/ui";
import { api, ApiError, fmtClock, fmtMin, MODE_LABEL, uid, type Json } from "@/lib/api";
import { audioState, downloadAudio, kvGet, kvSet, playableUrl, removeAudio } from "@/lib/offline";
import { usePlayer } from "@/lib/usePlayer";

export default function RadioPage() {
  return <Suspense fallback={<p className="note">読み込み中…</p>}><Radio /></Suspense>;
}

function Radio() {
  const app = useApp();
  const q = useSearchParams();
  const [prog, setProg] = useState<Json | null>(null);
  const [minutes, setMinutes] = useState(15);
  const [includeNew, setIncludeNew] = useState<boolean>(app.settings.radio_include_new ?? true);
  const [msg, setMsg] = useState<{ kind: "ok" | "err" | "warn"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const recent = useData<{ programs: Json[] }>(() => api("/radio/programs"), [app.mode]);

  useEffect(() => {
    const id = q.get("program"), unit = q.get("unit");
    (async () => {
      try {
        if (id) setProg(await api(`/radio/programs/${id}`));
        else if (unit) setProg(await api("/radio/programs", { body: { mode: app.mode, unit_ids: [unit] } }));
      } catch (e) {
        const er = e as ApiError;
        if (er.offline && id) { const cached = await kvGet<Json>(app.me.user_id, `program:${id}`); if (cached) { setProg(cached); return; } }
        setMsg({ kind: "err", text: er.detail });
      }
    })();
  }, [q, app.mode, app.me.user_id]);

  const create = async () => {
    setBusy(true); setMsg(null);
    try { setProg(await api("/radio/programs", { body: { mode: app.mode, minutes, include_new: includeNew } })); }
    catch (e) { const er = e as ApiError; setMsg({ kind: "warn", text: er.status === 422 ? "このモードにはラジオで聞ける教材がまだありません。既に学習した教材だけに絞っている場合は「未学習の教材も含める」をお試しください。" : er.detail }); }
    setBusy(false);
  };
  if (prog) return <ProgramPlayer key={prog.id} initial={prog} onClose={() => { setProg(null); recent.reload(); }} />;
  return (
    <>
      {msg && <Msg kind={msg.kind}>{msg.text}</Msg>}
      <section className="panel accent" aria-labelledby="radio-h">
        <h2 id="radio-h">ラジオ <small>{MODE_LABEL[app.mode]}</small></h2>
        <p className="note">本文・設問・選択肢・解答待ち・正答・解説をひと続きの音声で流します。開始後はタップ不要です。聞いただけでは正解・習得済みにはなりません。</p>
        <label className="field">長さの目安<select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>{[5, 10, 15, 20, 30].map((m) => <option key={m} value={m}>{m}分</option>)}</select></label>
        <label className="check"><input type="checkbox" checked={includeNew} onChange={(e) => setIncludeNew(e.target.checked)} />未学習の教材も含める</label>
        <p className="faint">ラジオには正答と解説が入るため、含まれた教材は以後「初見の成績」の対象外になります。</p>
        <button className="primary big" onClick={create} disabled={busy || !app.online}>{busy ? "番組を準備中…" : "番組を作って聞く"}</button>
      </section>
      <section className="panel"><h2>最近の番組</h2>
        {recent.data?.programs.length ? recent.data.programs.map((p) => (
          <p key={p.id} className="row"><span>{MODE_LABEL[p.mode]}・{p.units.length} Unit・{fmtMin(p.duration_ms / 1000)}・完了 {p.units.filter((u: Json) => u.complete).length}/{p.units.length}</span><button className="small" onClick={() => setProg(p)}>開く</button></p>
        )) : <p className="note">まだ番組がありません。</p>}
      </section>
    </>
  );
}

function ProgramPlayer({ initial, onClose }: { initial: Json; onClose: () => void }) {
  const app = useApp();
  const [prog, setProg] = useState<Json>(initial);
  const [dl, setDl] = useState<"none" | "ok" | "broken" | "busy">("none");
  const [msg, setMsg] = useState<{ kind: "ok" | "err" | "warn"; text: string } | null>(null);
  const [reports, setReports] = useState<Record<string, string>>({});
  const span = useRef<[number, number] | null>(null);   // range actually being played right now
  const queue = useRef<[number, number][]>([]);          // played ranges not yet confirmed by the server
  const lastPos = useRef(0);
  const [resumeAt, setResumeAt] = useState(0); // render-safe copy of the saved position
  const lastSent = useRef(0);
  const user = app.me.user_id;

  const flush = useCallback(async (force = false) => {
    if (span.current && span.current[1] > span.current[0]) { queue.current.push([Math.floor(span.current[0]), Math.ceil(span.current[1])]); span.current = [span.current[1], span.current[1]]; }
    if (!queue.current.length || (!force && Date.now() - lastSent.current < 10000)) return;
    lastSent.current = Date.now();
    const ranges = queue.current.splice(0);
    try {
      const r = await api<Json>(`/radio/programs/${prog.id}/progress`, { body: { event_id: uid(), ranges, position_ms: Math.floor(lastPos.current) } });
      setProg(r.program);
      if (r.awarded.length) setMsg({ kind: "ok", text: `+${r.awarded.reduce((a: number, b: Json) => a + b.amount, 0)} XP（実際に再生した範囲が90%以上の教材）` });
    } catch { queue.current.unshift(...ranges); } // offline: keep and resend later
    await kvSet(user, `radio-pos:${prog.id}`, lastPos.current);
  }, [prog.id, user]);

  const player = usePlayer({
    onTime: (ms, playing) => {
      // coverage = union of ranges that really played. A jump (seek) closes the current range.
      if (playing && span.current && Math.abs(ms - span.current[1]) < 1500 && ms >= span.current[1]) span.current[1] = ms;
      else if (playing) { if (span.current && span.current[1] > span.current[0]) queue.current.push([Math.floor(span.current[0]), Math.ceil(span.current[1])]); span.current = [ms, ms]; }
      lastPos.current = ms;
      flush();
    },
    onEnded: () => flush(true),
  });

  useEffect(() => {
    kvSet(user, `program:${prog.id}`, prog).catch(() => {});
    audioState(user, prog.audio.url, prog.audio).then(setDl);
    kvGet<number>(user, `radio-pos:${prog.id}`).then((p) => { lastPos.current = Math.max(p ?? 0, prog.position_ms ?? 0); setResumeAt(lastPos.current); });
    const leave = () => { flush(true); };
    const vis = () => { if (document.visibilityState === "hidden") flush(true); };
    window.addEventListener("pagehide", leave); document.addEventListener("visibilitychange", vis); window.addEventListener("online", leave);
    return () => { window.removeEventListener("pagehide", leave); document.removeEventListener("visibilitychange", vis); window.removeEventListener("online", leave); flush(true); };
  }, []);

  const start = async () => {
    setMsg(null);
    const { src } = await playableUrl(user, prog.audio.url);
    const from = lastPos.current >= prog.duration_ms - 1000 ? 0 : lastPos.current;
    span.current = null;
    const ok = await player.play(src, 1, from);
    if (!ok) return;
    if ("mediaSession" in navigator) {
      // feature-detected: lock-screen controls where the platform offers them
      try {
        navigator.mediaSession.metadata = new MediaMetadata({ title: `Listening Quest ラジオ（${MODE_LABEL[prog.mode]}）`, artist: `${prog.units.length} Unit`, album: "Listening Quest", artwork: [{ src: "/icon-512.png", sizes: "512x512", type: "image/png" }] });
        const set = (a: MediaSessionAction, h: MediaSessionActionHandler) => { try { navigator.mediaSession.setActionHandler(a, h); } catch {} };
        set("play", () => { player.play(); }); set("pause", () => player.pause());
        set("seekbackward", (d) => player.seek(Math.max(0, lastPos.current - (d.seekOffset ?? 10) * 1000)));
        set("seekforward", (d) => player.seek(Math.min(prog.duration_ms, lastPos.current + (d.seekOffset ?? 10) * 1000)));
        set("seekto", (d) => { if (d.seekTime != null) player.seek(d.seekTime * 1000); });
      } catch {}
    }
  };
  const download = async () => {
    setDl("busy"); setMsg(null);
    try {
      await downloadAudio(user, prog.audio.url, prog.audio);
      const st = await audioState(user, prog.audio.url, prog.audio);  // "downloaded" is shown only after verifying the stored copy
      setDl(st);
      if (st !== "ok") setMsg({ kind: "err", text: "保存したデータを確認できませんでした。もう一度お試しください。" });
    } catch (e) { setDl("none"); setMsg({ kind: "err", text: (e as Error).message }); }
  };
  const report = async (unitId: string, value: string) => {
    setReports({ ...reports, [unitId]: value });
    try { await api("/radio/self-reports", { body: { event_id: uid(), unit_id: unitId, value } }); } catch (e) { setMsg({ kind: "err", text: (e as ApiError).detail }); }
  };
  const relisten = async (unitId: string) => {
    try { const r = await api<Json>("/radio/relisten", { body: { unit_id: unitId } }); setMsg({ kind: "ok", text: `再聴の予定を ${r.due_day} に入れました。` }); } catch (e) { setMsg({ kind: "err", text: (e as ApiError).detail }); }
  };
  const pos = player.pos || resumeAt;
  const current = prog.units.find((u: Json) => pos >= u.start_ms && pos < u.end_ms);
  return (
    <>
      <section className="panel accent" aria-labelledby="pp-h">
        <h2 id="pp-h">ラジオ再生 <small>{MODE_LABEL[prog.mode]}・{prog.units.length} Unit・{fmtMin(prog.duration_ms / 1000)}</small></h2>
        <div className="row">
          {player.state === "playing" ? <button className="playbtn wide" onClick={player.pause}>❚❚ 一時停止</button>
            : <button className="playbtn wide primary" onClick={player.state === "paused" ? () => { player.play(); } : start} disabled={player.state === "loading"}>{player.state === "loading" ? "読み込み中…" : resumeAt > 1000 && player.state !== "ended" ? "▶ 続きから再生" : "▶ 再生"}</button>}
        </div>
        <label className="sr" htmlFor="seek">再生位置</label>
        <input id="seek" type="range" min={0} max={prog.duration_ms} value={Math.min(pos, prog.duration_ms)} onChange={(e) => { lastPos.current = Number(e.target.value); setResumeAt(lastPos.current); player.seek(Number(e.target.value)); }} />
        <p className="note" aria-live="polite">{fmtClock(pos)} / {fmtClock(prog.duration_ms)}　{player.state === "playing" ? "再生中" : player.state === "paused" ? "一時停止中" : player.state === "ended" ? "再生終了" : player.state === "loading" ? "読み込み中" : "停止中"}{current ? `・${current.title}` : ""}</p>
        {player.state === "error" && <Msg>{player.error} <button className="small" onClick={start}>再試行</button></Msg>}
        {msg && <Msg kind={msg.kind}>{msg.text}</Msg>}
        <div className="row">
          {dl === "ok" ? <><span className="chip">✓ 端末に保存済み（オフライン再生可）</span><button className="small" onClick={async () => { await removeAudio(user, prog.audio.url); setDl("none"); }}>保存を削除</button></>
            : dl === "broken" ? <><span className="chip demo">保存データが壊れています</span><button className="small" onClick={download}>再ダウンロード</button></>
            : <button className="small" onClick={download} disabled={dl === "busy" || !app.online}>{dl === "busy" ? "ダウンロード中…" : "オフライン用にダウンロード"}</button>}
          <button className="small ghost" onClick={() => { player.pause(); flush(true); onClose(); }}>番組を閉じる</button>
        </div>
        <p className="faint">端末のロック中や他のアプリ使用中に再生が続くかどうかは、端末とブラウザーの設定によります。通話などで止まった場合は「続きから再生」で再開できます。</p>
      </section>
      <section className="panel" aria-label="番組の内容">
        {prog.units.map((u: Json, i: number) => (
          <div className="qbox" key={u.unit_id}>
            <div className="row"><strong>{i + 1}. {u.title}</strong><DemoChip kind={u.audio_kind} /><span className="chip">{u.complete ? "✓ 聴取完了" : `再生 ${Math.round(u.coverage * 100)}%`}</span>
              <button className="small ghost" onClick={() => { lastPos.current = u.start_ms; setResumeAt(u.start_ms); player.seek(u.start_ms); }}>ここから</button></div>
            <div className="row" role="group" aria-label="自己申告（任意）"><small>自己申告（任意）:</small>
              {[["correct", "自分では正解"], ["incorrect", "不正解"], ["unknown", "不明"]].map(([v, l]) => <button key={v} className="small" aria-pressed={reports[u.unit_id] === v} disabled={!!reports[u.unit_id]} onClick={() => report(u.unit_id, v)}>{reports[u.unit_id] === v ? "✓ " : ""}{l}</button>)}
              <button className="small ghost" onClick={() => relisten(u.unit_id)}>あとで再聴</button></div>
          </div>
        ))}
        <p className="faint">自己申告は客観採点とは別に記録され、採点済みの結果を上書きしません。</p>
      </section>
    </>
  );
}
