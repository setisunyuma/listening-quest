"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError, MODE_LABEL, MODES, type Json, type Me } from "@/lib/api";
import { listPending, syncPending } from "@/lib/offline";

type Ctx = {
  me: Me; mode: string; settings: Json; setMode: (m: string) => Promise<void>; saveSettings: (p: Json) => Promise<void>;
  pending: number; pendingKnown: boolean; syncing: boolean; online: boolean; refreshPending: () => Promise<void>; syncNow: () => Promise<Json[]>; lastSync: Json[];
};
const AppCtx = createContext<Ctx | null>(null);
export const useApp = (): Ctx => {
  const c = useContext(AppCtx);
  if (!c) throw new Error("no app context");
  return c;
};

const TABS = [
  { href: "/", label: "ホーム", ic: "◆" }, { href: "/library", label: "教材", ic: "▦" }, { href: "/radio", label: "ラジオ", ic: "♪" }, { href: "/growth", label: "成長", ic: "↗" },
  { href: "/history", label: "履歴", ic: "≡", desktop: true }, { href: "/settings", label: "設定", ic: "⚙", desktop: true }, { href: "/admin", label: "教材管理", ic: "✎", desktop: true },
  { href: "/more", label: "メニュー", ic: "⋯", mobile: true },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [checked, setChecked] = useState(false);
  const [pending, setPending] = useState<number | null>(null); // null = not checked yet: never claim "synced" before knowing
  const [online, setOnline] = useState(true);
  const [lastSync, setLastSync] = useState<Json[]>([]);
  const [update, setUpdate] = useState<ServiceWorker | null>(null);

  const load = useCallback(async () => {
    try {
      const m = await api<Me>("/auth/me");
      setMe(m);
      localStorage.setItem("lq:me", JSON.stringify(m));
    } catch (e) {
      if ((e as ApiError).offline) {
        const cached = localStorage.getItem("lq:me");  // offline: keep working with the last known profile
        if (cached) setMe(JSON.parse(cached));
        setOnline(false);
      } else setMe(null);
    }
    setChecked(true);
  }, []);
  useEffect(() => { load(); }, [load]);

  const refreshPending = useCallback(async () => { if (me) setPending((await listPending(me.user_id)).length); }, [me]);
  const [syncing, setSyncing] = useState(false);
  const syncNow = useCallback(async () => {
    if (!me) return [];
    setSyncing(true);
    try {
      const r = await syncPending(me.user_id);
      if (r.sent) setLastSync(r.results);
      setOnline(true);
      await refreshPending();
      return r.results;
    } catch (e) {
      if ((e as ApiError).offline) setOnline(false);
      return [];
    } finally {
      setSyncing(false);
    }
  }, [me, refreshPending]);

  useEffect(() => {
    if (!me) return;
    refreshPending().then(() => { if (navigator.onLine) syncNow(); });
    const on = () => { setOnline(true); syncNow(); };
    const off = () => setOnline(false);
    const unauth = () => { setMe(null); };  // session expired: ask to sign in again, keep unsent answers
    const vis = () => { if (document.visibilityState === "visible" && navigator.onLine) syncNow(); };
    window.addEventListener("online", on); window.addEventListener("offline", off); window.addEventListener("lq:unauthorized", unauth); document.addEventListener("visibilitychange", vis);
    setOnline(navigator.onLine);
    return () => { window.removeEventListener("online", on); window.removeEventListener("offline", off); window.removeEventListener("lq:unauthorized", unauth); document.removeEventListener("visibilitychange", vis); };
  }, [me, refreshPending, syncNow]);

  useEffect(() => {
    if (!("serviceWorker" in navigator) || process.env.NODE_ENV !== "production") return;
    navigator.serviceWorker.register("/sw.js").then((reg) => {
      const watch = (w: ServiceWorker | null) => w?.addEventListener("statechange", () => { if (w.state === "installed" && navigator.serviceWorker.controller) setUpdate(w); });
      if (reg.waiting && navigator.serviceWorker.controller) setUpdate(reg.waiting);
      reg.addEventListener("updatefound", () => watch(reg.installing));
    }).catch(() => {});
    let reloaded = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => { if (!reloaded && sessionStorage.getItem("lq:sw-reload")) { reloaded = true; sessionStorage.removeItem("lq:sw-reload"); location.reload(); } });
  }, []);

  useEffect(() => {
    if (checked && !me && path !== "/login") router.replace("/login");
    if (checked && me && path === "/login") router.replace("/");
  }, [checked, me, path, router]);

  const settings = me?.settings ?? {};
  useEffect(() => {
    document.documentElement.style.setProperty("--scale", String(settings.font_scale || 1));
    document.body.classList.toggle("reduce", !!settings.reduce_effects);
    document.body.classList.toggle("side-left", settings.primary_side === "left");
  }, [settings.font_scale, settings.reduce_effects, settings.primary_side]);

  if (!checked) return <main><p className="note" role="status">読み込み中…</p></main>;
  if (path === "/login" || !me) return <LoginGate onDone={load} show={path === "/login"} />;

  const saveSettings = async (p: Json) => {
    const s = await api<Json>("/settings", { method: "PATCH", body: p });
    const next = { ...me, settings: s };
    setMe(next);
    localStorage.setItem("lq:me", JSON.stringify(next));
  };
  const ctx: Ctx = { me, mode: settings.active_mode, settings, setMode: (m) => saveSettings({ active_mode: m }), saveSettings, pending: pending ?? 0, pendingKnown: pending !== null, syncing, online, refreshPending, syncNow, lastSync };
  return (
    <AppCtx.Provider value={ctx}>
      <header className="top">
        <div className="brand">
          <div><p className="eyebrow">LISTENING QUEST</p><h1>{TABS.find((t) => t.href === path)?.label ?? (path === "/play" ? "学習" : path === "/check" ? "実力チェック" : "Listening Quest")}</h1></div>
          <div className="row">
            {me.realm === "demo" && <span className="chip demo" title="デモ用のデータ領域です。音声は動作確認用です。">デモ領域</span>}
            <span className={`badge-sync ${!online ? "off" : pending ? "pending" : ""}`} role="status" aria-live="polite">
              {!online ? "オフライン" : pending === null ? "同期を確認中" : syncing && pending ? `送信中 ${pending}件` : pending ? `未同期 ${pending}件` : "同期済み"}
            </span>
          </div>
        </div>
        <div className="modes" role="group" aria-label="モード切替">
          {MODES.map((m) => (<button key={m} aria-pressed={settings.active_mode === m} onClick={() => ctx.setMode(m)} disabled={!online}>{MODE_LABEL[m]}</button>))}
        </div>
        <nav className="tabs" aria-label="メイン">
          {TABS.map((t) => (
            <Link key={t.href} href={t.href} aria-current={path === t.href ? "page" : undefined} className={t.desktop ? "only-desktop" : t.mobile ? "only-mobile" : ""}>
              <span className="ic" aria-hidden="true">{t.ic}</span>{t.label}
            </Link>
          ))}
        </nav>
        {update && pending !== null && (
          <div className="msg warn" role="status">新しいバージョンがあります。{(pending ?? 0) > 0 && `未同期の回答 ${pending} 件は端末に残ります。`}
            <button className="small" onClick={() => { sessionStorage.setItem("lq:sw-reload", "1"); update.postMessage("skipWaiting"); }}>更新する</button></div>
        )}
      </header>
      <main>{children}</main>
    </AppCtx.Provider>
  );
}

function LoginGate({ onDone, show }: { onDone: () => Promise<void>; show: boolean }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  if (!show) return <main><p className="note" role="status">ログイン画面へ移動します…</p></main>;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      await api("/auth/login", { body: { username: u, password: p } });
      await onDone();
    } catch (e2) {
      const er = e2 as ApiError;
      setErr(er.status === 401 ? "ユーザー名またはパスワードが違います。" : er.status === 429 ? "試行回数が多すぎます。しばらく待ってからお試しください。" : er.detail);
    }
    setBusy(false);
  };
  return (
    <main>
      <form className="panel login" onSubmit={submit} aria-labelledby="login-h">
        <p className="eyebrow">LISTENING QUEST</p>
        <h2 id="login-h">ログイン</h2>
        <label className="field">ユーザー名<input name="username" autoComplete="username" autoCapitalize="none" value={u} onChange={(e) => setU(e.target.value)} required /></label>
        <label className="field">パスワード<input name="password" type="password" autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} required /></label>
        {err && <p className="msg err" role="alert">{err}</p>}
        <button className="primary wide big" disabled={busy}>{busy ? "確認中…" : "ログイン"}</button>
        <p className="faint">個人用アプリです。アカウントはサーバーのコマンドで作成します（README参照）。</p>
      </form>
    </main>
  );
}
