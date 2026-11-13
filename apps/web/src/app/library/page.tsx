"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { useApp } from "@/components/Shell";
import { BandChip, DemoChip, Msg, useData } from "@/components/ui";
import { api, fmtMin, MODE_LABEL, SKILL_LABEL, type LibraryCard } from "@/lib/api";

const STATUS = [["", "すべて"], ["new", "未学習"], ["learned", "学習済み"], ["review", "復習予定"], ["perfect_first", "初見全問正解"], ["perfect_review", "復習全問正解"]];
const TIME = [["", "すべて"], ["short", "1分以内"], ["mid", "1〜3分"], ["long", "3分以上"]];
const ART = ["linear-gradient(120deg,#134e4a,#0e7490)", "linear-gradient(120deg,#1e3a8a,#0e7490)", "linear-gradient(120deg,#4c1d95,#1e3a8a)", "linear-gradient(120deg,#7c2d12,#4c1d95)", "linear-gradient(120deg,#831843,#4c1d95)"];

export default function LibraryPage() {
  const app = useApp();
  // polled every few seconds: newly READY material appears without reopening the page
  const { data, error } = useData<{ units: LibraryCard[]; count: number }>(() => api(`/library?mode=${app.mode}`), [app.mode], 5000);
  const [part, setPart] = useState("");
  const [band, setBand] = useState("");
  const [time, setTime] = useState("");
  const [status, setStatus] = useState("");
  const units = useMemo(() => (data?.units ?? []).filter((u) =>
    (!part || u.part === part) && (!band || String(u.band) === band) &&
    (!time || (time === "short" ? u.est_sec <= 60 : time === "mid" ? u.est_sec > 60 && u.est_sec <= 180 : u.est_sec > 180)) &&
    (!status || (status === "review" ? u.status.startsWith("review") : status.startsWith("perfect") ? u.badge === status : u.status === status))
  ), [data, part, band, time, status]);
  const parts = useMemo(() => Array.from(new Map((data?.units ?? []).map((u) => [u.part, u.part_label])).entries()), [data]);
  const groups = useMemo(() => {
    const m = new Map<string, { label: string; items: LibraryCard[] }>();
    for (const u of units) { if (!m.has(u.part)) m.set(u.part, { label: u.part_label, items: [] }); m.get(u.part)!.items.push(u); }
    return Array.from(m.entries());
  }, [units]);
  if (!data) return error ? <Msg>{error}</Msg> : <p className="note" role="status">読み込み中…</p>;
  return (
    <>
      <section className="panel" aria-label="絞り込み">
        <p className="note">{MODE_LABEL[app.mode]} の教材 {data.count} 件{units.length !== data.count && `（表示 ${units.length} 件）`}</p>
        <div className="row">
          <label className="field">形式<select value={part} onChange={(e) => setPart(e.target.value)}><option value="">すべて</option>{parts.map(([p, l]) => <option key={p} value={p}>{l}</option>)}</select></label>
          <label className="field">難度<select value={band} onChange={(e) => setBand(e.target.value)}><option value="">すべて</option>{[1, 2, 3, 4, 5].map((b) => <option key={b}>{b}</option>)}</select></label>
          <label className="field">時間<select value={time} onChange={(e) => setTime(e.target.value)}>{TIME.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
          <label className="field">状態<select value={status} onChange={(e) => setStatus(e.target.value)}>{STATUS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        </div>
      </section>
      {data.count === 0 && <Msg kind="warn">このモードには、まだ学習できる教材がありません。「教材管理」で生成または取り込みをすると、準備ができ次第ここに自動で並びます。</Msg>}
      {data.count > 0 && units.length === 0 && <p className="note">条件に合う教材がありません。</p>}
      {groups.map(([p, g], gi) => (
        <details className="group" key={p} open={gi < 3 || !!part}>
          <summary><span className="gt">{g.label}</span><span className="chip">{g.items.length} 件</span><span className="chip">初見全問正解 {g.items.filter((u) => u.badge === "perfect_first").length}</span><span className="chev" aria-hidden="true">▶</span></summary>
          <div className="body cards">
            {g.items.map((u) => {
              const canAnswer = u.session_types.includes("train");
              const inner = (
                <>
                  <div className="art" style={{ background: ART[(u.band - 1) % 5] }} aria-hidden="true" />
                  <div className="title">{u.title}</div>
                  <div className="meta"><BandChip band={u.band} /><span className="chip">{fmtMin(u.est_sec)}</span><span className="chip">{u.question_count}問</span></div>
                  <div className="meta">
                    {u.badge ? <span className={`chip ${u.badge === "perfect_first" ? "rainbow" : "purple"}`}>{u.badge === "perfect_first" ? "★ " : "◆ "}{u.badge_label}</span> : null}
                    <span className="chip">{u.status_label}{u.due_day && u.status !== "review_due" ? ` ${u.due_day}` : ""}</span>
                    {!canAnswer && <span className="chip">ラジオ専用</span>}<DemoChip kind={u.audio_kind} />
                  </div>
                  <div className="faint">{SKILL_LABEL[u.primary_skill]}{u.last_score ? `・前回 ${u.last_score}` : ""}{u.presentation !== "official" ? "・音声向けアレンジ" : ""}</div>
                </>
              );
              return canAnswer ? <Link key={u.id} className={`card ${u.badge ?? u.status}`} href={`/play?unit=${u.id}`}>{inner}</Link> : <Link key={u.id} className={`card ${u.status}`} href={`/radio?unit=${u.id}`}>{inner}</Link>;
            })}
          </div>
        </details>
      ))}
      <p className="faint">難度は生成時の暫定評価です。虹色の枠は初見・規定条件内での全問正解、紫の枠は期限が来た復習での全問正解です。</p>
    </>
  );
}
