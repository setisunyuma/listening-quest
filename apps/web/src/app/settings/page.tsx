"use client";
import { useEffect, useRef, useState } from "react";
import { useApp } from "@/components/Shell";
import { Msg } from "@/components/ui";
import { api, ApiError, type Json } from "@/lib/api";
import { clearDevice, listPending } from "@/lib/offline";

export default function SettingsPage() {
  const app = useApp();
  const s = app.settings;
  const [msg, setMsg] = useState<{ kind: "ok" | "err" | "warn"; text: string } | null>(null);
  const [confirmOut, setConfirmOut] = useState<number | null>(null);
  const [usage, setUsage] = useState("");
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => { navigator.storage?.estimate?.().then((e) => setUsage(`${((e.usage ?? 0) / 1048576).toFixed(1)} MB 使用 / 約 ${((e.quota ?? 0) / 1048576).toFixed(0)} MB`)).catch(() => {}); }, []);
  const save = async (p: Json) => { try { await app.saveSettings(p); setMsg({ kind: "ok", text: "保存しました。" }); } catch (e) { setMsg({ kind: "err", text: (e as ApiError).detail }); } };
  const logout = async (force = false) => {
    const n = (await listPending(app.me.user_id)).length;
    if (n > 0 && !force) { setConfirmOut(n); return; }
    try { await api("/auth/logout", { body: {} }); } catch {}
    await clearDevice(app.me.user_id);
    localStorage.removeItem("lq:me");
    location.href = "/login";
  };
  const restore = async (f: File) => {
    try {
      const out = await api<Json>("/restore", { body: { export: JSON.parse(await f.text()), apply_settings: false } });
      if (!out.ok) setMsg({ kind: "err", text: `復元できませんでした: ${out.errors.join(" / ")}` });
      else setMsg({ kind: "ok", text: `復元しました。追加 ${Object.values(out.inserted as Record<string, number>).reduce((a, b) => a + b, 0)} 件・既存のためスキップ ${Object.values(out.skipped_existing as Record<string, number>).reduce((a, b) => a + b, 0)} 件・教材が無いため保留 ${Object.values(out.skipped_missing_unit as Record<string, number>).reduce((a, b) => a + b, 0)} 件。既存の記録は上書きしていません。` });
    } catch (e) { setMsg({ kind: "err", text: e instanceof SyntaxError ? "JSONファイルとして読み込めませんでした。" : (e as ApiError).detail }); }
  };
  return (
    <>
      {msg && <Msg kind={msg.kind}>{msg.text}</Msg>}
      <section className="panel"><h2>日課</h2>
        <label className="field">1日の目標時間（5モード合計）<select value={s.daily_goal_minutes} onChange={(e) => save({ daily_goal_minutes: Number(e.target.value) })}>{[10, 15, 20, 30, 45, 60].map((m) => <option key={m} value={m}>{m}分</option>)}</select></label>
        <label className="field">タイムゾーン<select value={s.timezone} onChange={(e) => save({ timezone: e.target.value })}>{Array.from(new Set(["Asia/Tokyo", s.timezone, Intl.DateTimeFormat().resolvedOptions().timeZone, "UTC", "America/Los_Angeles", "America/New_York", "Europe/London", "Australia/Sydney"])).map((z) => <option key={z}>{z}</option>)}</select></label>
        <label className="field">学習日の切り替え時刻<select value={s.day_cutoff_hour} onChange={(e) => save({ day_cutoff_hour: Number(e.target.value) })}>{Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}</select></label>
        <p className="faint">変更は次に作る計画から反映されます。進行中の計画の量は変わりません。日付はサーバーの時刻で判定します。</p>
      </section>
      <section className="panel"><h2>再生と表示</h2>
        <label className="check"><input type="checkbox" checked={!!s.radio_include_new} onChange={(e) => save({ radio_include_new: e.target.checked })} />ラジオに未学習の教材も含める</label>
        <label className="check"><input type="checkbox" checked={!!s.sfx} onChange={(e) => save({ sfx: e.target.checked })} />効果音（達成時の短い音）</label>
        <label className="check"><input type="checkbox" checked={!!s.reduce_effects} onChange={(e) => save({ reduce_effects: e.target.checked })} />演出と背景の動きを減らす</label>
        <label className="field">主な操作ボタンの位置<select value={s.primary_side} onChange={(e) => save({ primary_side: e.target.value })}><option value="right">右寄せ（標準）</option><option value="left">左寄せ（左手操作向け）</option></select></label>
        <label className="field">文字の大きさ<select value={s.font_scale} onChange={(e) => save({ font_scale: Number(e.target.value) })}><option value={0.9}>小</option><option value={1}>標準</option><option value={1.15}>大</option><option value={1.3}>特大</option></select></label>
      </section>
      <section className="panel"><h2>ホーム画面に追加（iPhone）</h2>
        <ol className="note"><li>Safariでこのページを開く</li><li>共有ボタン（□に↑）をタップ</li><li>「ホーム画面に追加」を選ぶ</li></ol>
        <p className="faint">追加後はアプリのように起動できます。ロック中の再生継続などは端末・OSの状態によって変わります。</p>
      </section>
      <section className="panel"><h2>データ</h2>
        <p className="note">同期状態: {app.online ? "オンライン" : "オフライン"}・未同期の回答 {app.pending} 件{usage && `・端末の保存量 ${usage}`}</p>
        <div className="row">
          <a className="btn" href="/api/export" download>学習履歴と設定を書き出す（JSON）</a>
          <button onClick={() => file.current?.click()}>書き出したファイルから復元</button>
          <input ref={file} type="file" accept="application/json,.json" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) restore(f); e.target.value = ""; }} />
          {app.pending > 0 && <button onClick={() => app.syncNow()} disabled={!app.online}>未同期の回答を今すぐ送信</button>}
        </div>
        <p className="faint">成績の正本はサーバーに保存されます。復元は内容を検証してから行い、既にある記録は上書き・二重登録しません。</p>
      </section>
      <section className="panel"><h2>アカウント</h2>
        <p className="note">{app.me.username}（{app.me.realm === "demo" ? "デモ領域" : "本番領域"}）</p>
        {confirmOut !== null ? (
          <div className="msg warn" role="alertdialog" aria-label="ログアウトの確認">まだ送信していない回答が {confirmOut} 件あります。ログアウトすると端末から削除され、採点・記録されません。
            <div className="row"><button onClick={async () => { await app.syncNow(); setConfirmOut(null); }} disabled={!app.online}>先に送信する</button><button className="danger" onClick={() => logout(true)}>削除してログアウト</button><button className="ghost" onClick={() => setConfirmOut(null)}>やめる</button></div></div>
        ) : <button className="danger" onClick={() => logout()}>ログアウト（端末の保存データを削除）</button>}
      </section>
    </>
  );
}
