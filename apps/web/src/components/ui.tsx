"use client";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError, type Json } from "@/lib/api";

/** Fetch with optional polling. Keeps the last good data while offline. */
export function useData<T = Json>(fn: () => Promise<T>, deps: unknown[], pollMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const f = useRef(fn);
  useLayoutEffect(() => { f.current = fn; });
  const reload = useCallback(async () => {
    try {
      setData(await f.current());
      setError("");
    } catch (e) {
      setError((e as ApiError).detail || "読み込めませんでした。");
    }
  }, []);
  useEffect(() => {
    let alive = true;
    setData(null);
    reload();
    if (!pollMs) return;
    const t = setInterval(() => { if (alive && document.visibilityState === "visible") reload(); }, pollMs);
    return () => { alive = false; clearInterval(t); };
  }, deps);
  return { data, error, reload, setData };
}

export function Msg({ kind = "err", children }: { kind?: "err" | "ok" | "warn"; children: React.ReactNode }) {
  if (!children) return null;
  return <p className={`msg ${kind}`} role={kind === "err" ? "alert" : "status"}>{children}</p>;
}

export function BandChip({ band }: { band: number }) {
  return <span className={`chip b${band}`} title="モード内の暫定難度帯（生成時の目安で、校正済みの難度ではありません）">難度 {band}</span>;
}

export function DemoChip({ kind }: { kind: string }) {
  return kind === "demo_tone" ? <span className="chip demo" title="ビープ音による動作確認用の音声です。実用のリスニング教材ではありません。">音声動作確認用</span> : null;
}

export function Figure({ fig, highlight = [] }: { fig: Json; highlight?: string[] }) {
  // Rendered from structured data as plain table cells. No HTML from material is ever injected.
  return (
    <figure style={{ margin: "10px 0" }}>
      {fig.title && <figcaption className="note">{fig.title}</figcaption>}
      <div className="table-wrap">
        <table className="fig">
          {fig.columns?.length > 0 && <thead><tr>{fig.columns.map((c: string, i: number) => <th key={i} scope="col">{c}</th>)}</tr></thead>}
          <tbody>{fig.rows.map((r: Json) => (<tr key={r.id} className={highlight.includes(r.id) ? "hl" : ""}>{r.cells.map((c: Json) => <td key={c.id} className="en">{c.text}</td>)}</tr>))}</tbody>
        </table>
      </div>
      {fig.note && <p className="faint">{fig.note}</p>}
    </figure>
  );
}

export function Ring({ done, total }: { done: number; total: number }) {
  const r = 44, c = 2 * Math.PI * r, ratio = total ? Math.min(1, done / total) : 0;
  return (
    <svg className="ring" viewBox="0 0 108 108" role="img" aria-label={`今日の進捗 ${done} / ${total}`}>
      <defs><linearGradient id="ringgrad" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#5eead4" /><stop offset=".6" stopColor="#22d3ee" /><stop offset="1" stopColor="#38bdf8" /></linearGradient></defs>
      <circle className="bg" cx="54" cy="54" r={r} />
      <circle className="fg" cx="54" cy="54" r={r} strokeDasharray={c} strokeDashoffset={c * (1 - ratio)} transform="rotate(-90 54 54)" />
      <text x="54" y="61" textAnchor="middle">{done}/{total}</text>
    </svg>
  );
}

/** Short optional effect sound (off by default). Never plays over material: callers use it only after audio ended. */
export function chime(enabled: boolean) {
  if (!enabled) return;
  try {
    const AC = window.AudioContext || (window as Json).webkitAudioContext;
    const ctx = new AC();
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.value = 880; g.gain.value = 0.05;
    o.connect(g); g.connect(ctx.destination);
    o.start(); o.stop(ctx.currentTime + 0.12);
    o.onended = () => ctx.close();
  } catch {}
}
