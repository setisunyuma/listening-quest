"use client";
// One HTMLAudioElement wrapper. The UI state follows what the element actually does: "playing" is only
// reported after the browser fired `playing`; a rejected play() (autoplay policy, bad file, offline)
// becomes an error state with a message - never a silent "playing".
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

export type PlayerState = "idle" | "loading" | "playing" | "paused" | "ended" | "error";

export function usePlayer(opts: { onEnded?: () => void; onStarted?: () => void; onTime?: (ms: number, playing: boolean) => void } = {}) {
  const ref = useRef<HTMLAudioElement | null>(null);
  const cb = useRef(opts);
  useLayoutEffect(() => { cb.current = opts; });
  const [state, setState] = useState<PlayerState>("idle");
  const [error, setError] = useState("");
  const [pos, setPos] = useState(0);
  const [dur, setDur] = useState(0);

  useEffect(() => {
    const a = new Audio();
    a.preload = "auto";
    ref.current = a;
    const on = (ev: string, fn: () => void) => a.addEventListener(ev, fn);
    on("playing", () => { setState("playing"); setError(""); cb.current.onStarted?.(); });
    on("pause", () => { if (!a.ended) setState((s) => (s === "error" ? s : "paused")); });
    on("waiting", () => setState((s) => (s === "playing" ? "loading" : s)));
    on("ended", () => { setState("ended"); cb.current.onEnded?.(); });
    on("timeupdate", () => { setPos(a.currentTime * 1000); cb.current.onTime?.(a.currentTime * 1000, !a.paused); });
    on("durationchange", () => setDur(Number.isFinite(a.duration) ? a.duration * 1000 : 0));
    on("error", () => { setState("error"); setError("音声を読み込めませんでした。通信状態を確認して、もう一度お試しください。"); });
    return () => { a.pause(); a.removeAttribute("src"); a.load(); };
  }, []);

  const play = useCallback(async (src?: string, rate = 1, from?: number) => {
    const a = ref.current;
    if (!a) return false;
    if (src && (a.getAttribute("data-src") !== src || a.error)) {
      a.src = src; // also after a failed load: retry fetches the file again
      a.setAttribute("data-src", src);
      a.load();
    }
    if (from !== undefined) { try { a.currentTime = from / 1000; } catch {} }
    a.playbackRate = rate;
    setState("loading");
    try {
      await a.play();
      return true;
    } catch (e) {
      const name = (e as DOMException)?.name;
      setState("error");
      setError(name === "NotAllowedError" ? "再生がブロックされました。再生ボタンをタップしてください。" : name === "AbortError" ? "再生が中断されました。もう一度タップしてください。" : "音声を再生できませんでした。通信状態を確認して、もう一度お試しください。");
      return false;
    }
  }, []);
  const pause = useCallback(() => ref.current?.pause(), []);
  const seek = useCallback((ms: number) => { if (ref.current) { try { ref.current.currentTime = ms / 1000; } catch {} } }, []);
  const stop = useCallback(() => { const a = ref.current; if (a) { a.pause(); try { a.currentTime = 0; } catch {} setState("idle"); } }, []);
  return { state, error, pos, dur, play, pause, seek, stop, el: ref };
}
