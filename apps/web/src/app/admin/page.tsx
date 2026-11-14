"use client";
import { useRef, useState } from "react";
import { useApp } from "@/components/Shell";
import { Msg, useData } from "@/components/ui";
import { api, ApiError, fmtMin, MODE_LABEL, SKILL_LABEL, type Json } from "@/lib/api";

const JOB: Record<string, string> = { QUEUED: "待機中", RUNNING: "実行中", BLOCKED: "保留中", RETRY_WAIT: "再試行待ち", FAILED: "失敗", SUCCEEDED: "完了" };
const UNIT: Record<string, string> = { DRAFT: "下書き", CONTENT_REVIEW: "内容確認中", AUDIO_PENDING: "音声待ち", AUDIO_GENERATING: "音声生成中", AUDIO_QA: "音声確認中", READY: "利用可能", QUARANTINED: "隔離", FAILED: "失敗" };
const STEP: Record<string, string> = { content: "問題作成", review: "内容検品", audio: "音声生成", ready: "公開" };
const REVIEW: Record<string, string> = { api_reviewed: "API検品", manual_reviewed: "手動承認", author_reviewed: "作成者確認", demo_mock_reviewed: "デモ用の模擬検品", audio_auto_checked: "音声の自動チェック", audio_human_listened: "人が聞いて確認", audio_demo_mock_checked: "デモ用の模擬チェック" };

export default function AdminPage() {
  const app = useApp();
  const [msg, setMsg] = useState<{ kind: "ok" | "err" | "warn"; text: string } | null>(null);
  const over = useData<Json>(() => api("/admin/overview"), [app.mode], 10000);
  const jobs = useData<{ jobs: Json[] }>(() => api("/admin/jobs"), [], 3000);
  const units = useData<{ units: Json[] }>(() => api("/admin/units"), [], 6000);
  const mock = useData<Json>(() => api(`/admin/mock?mode=${app.mode}`), [app.mode], 6000);
  const meta = useData<Json>(() => api("/meta"), []);
  const [gen, setGen] = useState({ part: "", primary_skill: "explicit_detail", band: 3, count: 1, topic_hint: "" });
  const [budget, setBudget] = useState<Json | null>(null);
  const manifest = useRef<HTMLInputElement>(null);
  const files = useRef<HTMLInputElement>(null);
  const [report, setReport] = useState<Json | null>(null);

  const run = async (fn: () => Promise<string | void>) => {
    setMsg(null);
    try { const t = await fn(); if (t) setMsg({ kind: "ok", text: t }); } catch (e) { setMsg({ kind: "err", text: (e as ApiError).detail }); }
    over.reload(); jobs.reload(); units.reload(); mock.reload();
  };
  const parts: Json[] = meta.data?.modes.find((m: Json) => m.mode === app.mode)?.parts ?? [];
  const sys = over.data?.settings;
  const b = budget ?? sys;
  const active = (jobs.data?.jobs ?? []).filter((j) => j.status !== "SUCCEEDED");
  const waiting = (units.data?.units ?? []).filter((u) => u.status !== "READY" || u.invalidated);
  return (
    <>
      {msg && <Msg kind={msg.kind}>{msg.text}</Msg>}
      {over.data && (
        <section className="panel" aria-labelledby="bud-h">
          <h2 id="bud-h">有料生成と予算</h2>
          {app.me.realm === "demo" ? <p className="note">デモ領域では無料のデモ用プロバイダーを使います（音声は動作確認用のトーン）。有料APIは呼びません。</p> : (
            <>
              <p className="note">APIキー: {over.data.api_key_present ? "サーバーに設定済み" : "未設定（取り込みと手動承認は利用できます）"}。キーがあるだけでは有料処理は始まりません。</p>
              <dl className="kv"><dt>本日の利用（UTC）</dt><dd>確定 ${over.data.spend_today.settled_usd.toFixed(4)}・実行中の予約 ${over.data.spend_today.reserved_usd.toFixed(4)}・確定できない分 ${over.data.spend_today.unknown_usd.toFixed(4)}</dd>
                {over.data.spend_month && <><dt>今月の利用（{over.data.spend_month.month}）</dt><dd>計上 ${over.data.spend_month.counted_usd.toFixed(4)}{over.data.settings.monthly_budget_usd != null && <> / 月の予算 ${over.data.settings.monthly_budget_usd}</>}</dd></>}
                <dt>モデル</dt><dd>{over.data.providers.content_model} / {over.data.providers.tts_model} / {over.data.providers.asr_model}</dd><dt>単価の出典</dt><dd>{over.data.prices.source}（確認日 {over.data.prices.checked_at}）</dd></dl>
            </>
          )}
          {b && (
            <div className="row" style={{ alignItems: "flex-end" }}>
              {app.me.realm !== "demo" && <>
                <label className="field">1日の予算（USD）<input type="number" min={0} step={0.1} value={b.daily_budget_usd} onChange={(e) => setBudget({ ...b, daily_budget_usd: Number(e.target.value) })} /></label>
                <label className="field">月の予算（USD）<input type="number" min={0} step={1} value={b.monthly_budget_usd ?? ""} onChange={(e) => setBudget({ ...b, monthly_budget_usd: Number(e.target.value) })} /></label>
                <label className="field">1ジョブの上限（USD）<input type="number" min={0} step={0.05} value={b.per_job_cap_usd} onChange={(e) => setBudget({ ...b, per_job_cap_usd: Number(e.target.value) })} /></label>
              </>}
              <label className="field">同時実行数<input type="number" min={1} max={8} value={b.max_concurrent_jobs} onChange={(e) => setBudget({ ...b, max_concurrent_jobs: Number(e.target.value) })} /></label>
              <button onClick={() => run(async () => { await api("/admin/system", { method: "PATCH", body: { daily_budget_usd: b.daily_budget_usd, monthly_budget_usd: b.monthly_budget_usd, per_job_cap_usd: b.per_job_cap_usd, max_concurrent_jobs: b.max_concurrent_jobs } }); setBudget(null); return "上限を保存しました。"; })}>上限を保存</button>
            </div>
          )}
          {sys && (
            <div className="row">
              {app.me.realm !== "demo" && <label className="check"><input type="checkbox" checked={sys.paid_enabled} onChange={(e) => run(async () => { await api("/admin/system", { method: "PATCH", body: { paid_enabled: e.target.checked } }); return e.target.checked ? `有料生成を有効にしました（1日 $${sys.daily_budget_usd} まで）。この範囲内の自動補充は毎回の承認なしで実行されます。` : "有料生成を止めました。既存の教材で学習を続けられます。"; })} />有料生成を有効にする（1日 ${sys.daily_budget_usd}・月 ${sys.monthly_budget_usd}・1ジョブ ${sys.per_job_cap_usd} まで）</label>}
              <label className="check"><input type="checkbox" checked={sys.auto_replenish} onChange={(e) => run(async () => { await api("/admin/system", { method: "PATCH", body: { auto_replenish: e.target.checked } }); })} />在庫の自動補充</label>
              <button className="small" onClick={() => run(async () => { const r = await api<Json>("/admin/replenish/run", { body: {} }); return r.queued ? `${r.queued}件の生成を予約しました。` : `補充は行いませんでした: ${r.reason}`; })}>今すぐ在庫を確認</button>
            </div>
          )}
          <p className="note">未学習の在庫（{MODE_LABEL[app.mode]}）: {over.data.inventory.untouched_units} Unit・{fmtMin(over.data.inventory.untouched_sec)}（約 {over.data.inventory.days_of_stock} 日分）・生成中 {over.data.inventory.jobs_in_progress} 件</p>
          <p className="faint">{over.data.inventory.by_part.map((p: Json) => `${p.label_ja} ${p.units}`).join(" / ")}</p>
          {app.me.realm !== "demo" && <p className="faint">{over.data.disclaimer}</p>}
        </section>
      )}

      {app.me.realm !== "demo" && <Costs />}
      <Reports />
      {app.me.realm === "live" && <VerifyLive />}

      <section className="panel" aria-labelledby="gen-h">
        <h2 id="gen-h">教材を生成 <small>{MODE_LABEL[app.mode]}</small></h2>
        <div className="row" style={{ alignItems: "flex-end" }}>
          <label className="field">形式<select value={gen.part} onChange={(e) => setGen({ ...gen, part: e.target.value })}><option value="">選択</option>{parts.map((p) => <option key={p.part} value={p.part}>{p.label_ja}</option>)}</select></label>
          <label className="field">技能<select value={gen.primary_skill} onChange={(e) => setGen({ ...gen, primary_skill: e.target.value })}>{Object.entries(SKILL_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label className="field">難度帯<select value={gen.band} onChange={(e) => setGen({ ...gen, band: Number(e.target.value) })}>{[1, 2, 3, 4, 5].map((x) => <option key={x}>{x}</option>)}</select></label>
          <label className="field">数量<input type="number" min={1} max={20} value={gen.count} onChange={(e) => setGen({ ...gen, count: Number(e.target.value) })} style={{ width: 90 }} /></label>
          <label className="field">話題（任意）<input value={gen.topic_hint} onChange={(e) => setGen({ ...gen, topic_hint: e.target.value })} maxLength={120} /></label>
          <button className="primary" disabled={!gen.part} onClick={() => run(async () => { const r = await api<Json>("/admin/jobs", { body: { ...gen, mode: app.mode } }); return `${r.job_ids.length}件の生成を予約しました。画面を閉じても処理は続きます。`; })}>生成を予約</button>
        </div>
      </section>

      <section className="panel" aria-labelledby="job-h">
        <h2 id="job-h">生成・取り込みの状況 <small>進行中 {active.length} 件</small></h2>
        {(jobs.data?.jobs.length ?? 0) === 0 ? <p className="note">ジョブはまだありません。</p> : (
          <div className="table-wrap"><table className="list">
            <thead><tr><th scope="col">内容</th><th scope="col">状態</th><th scope="col">進み具合・理由</th><th scope="col">費用</th><th scope="col"><span className="sr">操作</span></th></tr></thead>
            <tbody>{jobs.data!.jobs.slice(0, 40).map((j) => (
              <tr key={j.id}>
                <td>{MODE_LABEL[j.params.mode] ?? ""} {j.params.part}<br /><small>{j.unit_title ?? (j.kind === "generate" ? "生成" : "取り込み")}{j.params.pool === "assessment" ? "・実力チェック用" : ""}</small></td>
                <td><span className={`chip ${j.status === "FAILED" ? "demo" : ""}`}>{JOB[j.status]}</span>{j.unit_status && <><br /><small>教材: {UNIT[j.unit_status]}</small></>}</td>
                <td><small>{j.last_completed_step ? `完了した工程: ${STEP[j.last_completed_step] ?? j.last_completed_step}` : "未着手"}{j.status === "BLOCKED" ? <><br />{j.blocked_label ?? j.blocked_reason}</> : null}
                  {(j.status === "FAILED" || j.status === "RETRY_WAIT") && <><br />{j.unit_status_reason ?? j.stop_detail}（試行 {j.attempts}/{j.max_attempts}）</>}</small></td>
                <td><small>{j.cost.requests ? `確定 $${j.cost.settled_usd.toFixed(4)}${j.cost.unknown_usd ? `・不明 $${j.cost.unknown_usd.toFixed(4)}` : ""}` : "—"}</small></td>
                <td>{["FAILED", "BLOCKED", "RETRY_WAIT"].includes(j.status) && j.unit_status !== "QUARANTINED" && <button className="small" onClick={() => run(async () => { await api(`/admin/jobs/${j.id}/retry`, { body: {} }); return "再試行を予約しました。"; })}>再試行</button>}</td>
              </tr>
            ))}</tbody></table></div>
        )}
      </section>

      <section className="panel" aria-labelledby="imp-h">
        <h2 id="imp-h">JSONから取り込み</h2>
        <p className="note">manifest JSON（またはUnit JSON）と、対応する音声ファイルを選びます。ファイル名とハッシュを照合します。JSON内の「検品済み」表記だけでは承認されません。</p>
        <div className="row" style={{ alignItems: "flex-end" }}>
          <label className="field">JSONファイル<input ref={manifest} type="file" accept="application/json,.json" /></label>
          <label className="field">音声ファイル（任意・複数可）<input ref={files} type="file" accept="audio/*,.mp3,.wav,.m4a,.ogg,.flac" multiple /></label>
          <button onClick={() => run(async () => {
            const m = manifest.current?.files?.[0];
            if (!m) throw new ApiError(0, "JSONファイルを選んでください。");
            const fd = new FormData();
            fd.append("manifest", m);
            for (const f of Array.from(files.current?.files ?? [])) fd.append("files", f);
            const r = await api<Json>("/admin/import", { form: fd });
            setReport(r);
            return `取り込み結果: ${{ accepted: "受け付けました", duplicate: "同じ版が登録済みです（重複登録なし）", conflict: "同じID・版で内容が異なります（競合）", rejected: "受け付けられませんでした" }[r.status as string]}`;
          })}>取り込む</button>
        </div>
        {report && (<div className="evidence">
          {report.errors.map((e: string, i: number) => <p key={i} className="msg err">{e}</p>)}
          {report.units.map((u: Json, i: number) => <p key={i}><strong>{u.unit}</strong>: {{ created: "登録", duplicate: "登録済み", conflict: "競合", rejected: "不可" }[u.status as string]} <small>{(u.errors ?? []).join(" / ")}{u.audio ? ` ${u.audio.note}` : ""}</small></p>)}
          {report.notes.map((n: Json, i: number) => <p key={i} className="note">{n.item}: {n.reason}</p>)}
        </div>)}
      </section>

      <section className="panel" aria-labelledby="mock-h">
        <h2 id="mock-h">{mock.data?.inventory.exam_label ?? "実力チェック"}セット <small>{MODE_LABEL[app.mode]}</small></h2>
        {mock.data && (<>
          <p className="note">実力チェック用に確保した未接触の教材だけで組み立てます。全形式がそろうまで組み立てません（短縮版は作りません）。</p>
          <p className="faint">{mock.data.inventory.parts.map((p: Json) => `${p.label_ja} ${p.have}/${p.need}${p.in_progress ? `(+${p.in_progress}生成中)` : ""}`).join(" / ")}</p>
          <div className="row">
            <button onClick={() => run(async () => { const r = await api<Json>("/admin/mock/generate", { body: { mode: app.mode } }); return r.queued ? `不足分 ${r.queued} 件の生成を予約しました。` : "不足はありません（または生成中です）。"; })}>不足分を生成</button>
            <button className="primary" disabled={!mock.data.inventory.complete} onClick={() => run(async () => { const r = await api<Json>("/admin/mock/assemble", { body: { mode: app.mode } }); if (!r.assembled) throw new ApiError(0, r.message); return `セットを作成しました（${r.set.slots}問・所要 ${fmtMin(r.set.duration_sec)}）。`; })}>セットを組み立てる</button>
          </div>
          {mock.data.sets.map((s: Json) => <p key={s.id}>{s.label}・{s.slots}問・{fmtMin(s.duration_sec)} <span className="chip">{{ READY: "開始できます", USED: "使用済み", STALE: "無効（接触済みの教材あり）" }[s.status as string]}</span></p>)}
        </>)}
      </section>

      <section className="panel" aria-labelledby="unit-h">
        <h2 id="unit-h">教材の状態 <small>確認が必要 {waiting.length} 件 / 全 {units.data?.units.length ?? 0} 件</small></h2>
        <div className="table-wrap"><table className="list">
          <thead><tr><th scope="col">教材</th><th scope="col">状態</th><th scope="col">確認の記録</th><th scope="col"><span className="sr">操作</span></th></tr></thead>
          <tbody>{[...waiting, ...(units.data?.units ?? []).filter((u) => u.status === "READY" && !u.invalidated)].slice(0, 60).map((u) => (
            <tr key={u.id}>
              <td>{u.title}<br /><small>{MODE_LABEL[u.mode]}・{u.part_label}・{u.source}/{u.content_id} 版{u.revision}{u.pool === "assessment" ? "・実力チェック用" : ""}</small></td>
              <td><span className="chip">{u.invalidated ? "無効化済み" : UNIT[u.status]}</span>{u.status === "READY" && <><br /><small>{u.audio_kind === "demo_tone" ? "音声動作確認用" : { auto_checked: "音声: 自動チェック済み", human_listened: "音声: 人が聞いて確認", mock_checked: "音声: デモ用模擬チェック" }[u.audio_review_state as string]}</small></>}
                {u.status_reason && <><br /><small>{u.status_reason.slice(0, 160)}</small></>}{u.invalidated_reason && <><br /><small>{u.invalidated_reason}</small></>}</td>
              <td><small>{u.reviews.map((r: Json) => `${REVIEW[r.method] ?? r.method}${r.result === "pass" ? "" : "（不一致）"}`).join("・") || "なし"}</small></td>
              <td><div className="row">
                {u.status === "CONTENT_REVIEW" && <button className="small" onClick={() => { if (window.confirm("本文・正答・根拠・解説を自分で確認しましたか？ この版の内容を承認します。")) run(async () => { await api(`/admin/units/${u.id}/approve`, { body: { target: "content" } }); return "内容を承認しました。"; }); }}>内容を承認</button>}
                {u.status === "AUDIO_QA" && <button className="small" onClick={() => { if (window.confirm("音声を実際に聞いて、台本どおりであることを確認しましたか？")) run(async () => { await api(`/admin/units/${u.id}/approve`, { body: { target: "audio" } }); return "音声を承認しました。"; }); }}>音声を承認</button>}
                {!u.invalidated && u.status === "READY" && <button className="small danger" onClick={() => { const reason = window.prompt("無効化する理由（例: 正答が誤っている）"); if (reason) run(async () => { await api(`/admin/units/${u.id}/invalidate`, { body: { reason } }); return "無効化しました。履歴は残り、成績と出題候補から除外して再計算しました。"; }); }}>無効化</button>}
              </div></td>
            </tr>
          ))}</tbody></table></div>
      </section>
    </>
  );
}

function VerifyLive() {
  const st = useData<Json>(() => api("/admin/verify-live"), [], 4000);
  const [msg, setMsg] = useState<{ kind: "ok" | "err" | "warn"; text: string } | null>(null);
  const d = st.data;
  const r = d?.report;
  const usd = (v: number) => `$${(v ?? 0).toFixed(4)}`;
  const start = async () => {
    setMsg(null);
    if (!window.confirm(`共通テスト第3問の教材を1件だけ、実際のAPIで生成・検品・音声生成・音声確認します。\n上限: 検証予算の残り ${usd(d.budget.remaining_usd)}（すべての工程と再試行を含む）。見積もりの上限は ${usd(d.expected.total_usd)} です。\n開始しますか？`)) return;
    try { await api("/admin/verify-live", { body: {} }); setMsg({ kind: "ok", text: "開始しました。画面を閉じても処理は続きます。" }); st.reload(); }
    catch (e) { setMsg({ kind: "err", text: (e as ApiError).detail }); }
  };
  if (!d) return null;
  const b = d.budget;
  return (
    <section className="panel accent" aria-labelledby="ver-h">
      <h2 id="ver-h">実APIで1教材を検証</h2>
      <p className="note">{d.spec.label_ja}を1件だけ、実際のAPIで「問題作成→検品→音声生成（{d.tts_provider === "openai_speech" ? "HTTP音声" : "Realtime音声"}）→音声認識による確認」まで行います。全体の有料生成はOFFのままで、登録済みの例題や自動補充は処理されません。</p>
      <dl className="kv">
        <dt>検証予算</dt><dd>合計 {usd(b.total_usd)}・他の環境で使用済み {usd(b.prior_environments_usd)}・この環境で計上 {usd(b.this_environment.counted_usd)}・<strong>残り {usd(b.remaining_usd)}</strong></dd>
        <dt>1件の見積もり（上限側）</dt><dd>{usd(d.expected.total_usd)}</dd>
      </dl>
      {d.running ? <Msg kind="warn">検証を実行中です（上限 {usd(d.cap_usd)}）。数分かかります。</Msg> : (
        <>
          {d.problems.map((p: string) => <Msg key={p} kind="warn">{p === "OPENAI_API_KEY is not set on the server" ? "サーバーにAPIキーが設定されていません。" : p}</Msg>)}
          <button className="primary" onClick={start} disabled={d.problems.length > 0}>検証を開始（上限 {usd(b.remaining_usd)}）</button>
          <p className="faint">上限は、このアプリが新しいリクエストを始めないための管理です。応答が途切れたリクエストは見積額で計上し、課金されていないとはみなしません。実際の請求額は OpenAI の請求画面が正です。</p>
        </>
      )}
      {msg && <Msg kind={msg.kind}>{msg.text}</Msg>}
      {r && (
        <div className="evidence">
          <p><strong>直近の検証</strong>: ジョブ {r.job.status === "SUCCEEDED" ? "完了" : r.job.status === "FAILED" ? "失敗" : r.job.status === "RUNNING" ? "実行中" : r.job.status}・教材 {r.unit_status ?? "未作成"}{r.last_completed_step ? `（完了した工程: ${r.last_completed_step}）` : ""}</p>
          {r.unit_status_reason && <p className="note">理由: {r.unit_status_reason.slice(0, 300)}</p>}
          {r.stop_detail && r.job.status !== "SUCCEEDED" && <p className="note">停止理由: {r.stop_detail.slice(0, 300)}</p>}
          <p>費用: 確定（APIの使用量から）{usd(r.cost.settled_usd)}・推計（使用量の返らないAPI）{usd(r.cost.computed_usd)}・不明（応答なし、見積額で計上）{usd(r.cost.unknown_usd)}・予約中 {usd(r.cost.reserved_usd)}（{r.cost.requests} リクエスト） <small>単価の出典 {r.price_checked_at}</small></p>
          {r.audio_check && <p>音声確認（{r.audio_check.method === "audio_auto_checked" ? "独立した音声認識" : r.audio_check.method}）: {r.audio_check.result === "pass" ? "合格" : "不合格"}・{r.audio_check.clips?.length ?? 0} クリップ{r.audio_check.mismatches?.length ? `・不一致 ${r.audio_check.mismatches.length}` : ""}</p>}
          {r.open_in_app && <a className="btn primary" href={r.open_in_app}>アプリで再生して解く</a>}
        </div>
      )}
    </section>
  );
}


function Reports() {
  const { data, reload } = useData<{ reports: Json[] }>(() => api("/admin/reports"), [], 15000);
  const [msg, setMsg] = useState("");
  const act = async (id: string, action: string) => {
    try { await api(`/admin/reports/${id}/resolve`, { body: { action } }); setMsg({ dismiss: "対応済みにしました。", withdraw: "教材を出題から外しました。", restore: "教材を元に戻しました。" }[action] ?? ""); reload(); }
    catch (e) { setMsg((e as ApiError).detail); }
  };
  if (!data || data.reports.length === 0) return null;
  return (
    <section className="panel" aria-labelledby="rep-h">
      <h2 id="rep-h">報告 <small>{data.reports.length} 件</small></h2>
      <p className="note">解説画面から送った報告です。「答え・解説がおかしい」「音声がおかしい」は送った時点で出題から外れています。</p>
      {msg && <Msg kind="ok">{msg}</Msg>}
      <div className="table-wrap"><table className="list">
        <thead><tr><th scope="col">教材</th><th scope="col">報告</th><th scope="col"><span className="sr">操作</span></th></tr></thead>
        <tbody>{data.reports.map((r) => (
          <tr key={r.id}>
            <td>{r.title}<br /><small>{MODE_LABEL[r.mode]} {r.part}・{new Date(r.created_at).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</small></td>
            <td><span className="chip">{r.kind_label}</span>{r.unit_withdrawn && <span className="chip demo">出題停止中</span>}{r.note && <><br /><small>{r.note}</small></>}</td>
            <td><div className="row">
              {r.unit_withdrawn ? <button className="small" onClick={() => act(r.id, "restore")}>元に戻す</button> : <button className="small danger" onClick={() => act(r.id, "withdraw")}>出題から外す</button>}
              <button className="small" onClick={() => act(r.id, "dismiss")}>対応済みにする</button>
            </div></td>
          </tr>
        ))}</tbody></table></div>
    </section>
  );
}


function Costs() {
  const { data } = useData<Json>(() => api("/admin/costs"), [], 60000);
  if (!data) return null;
  const days = (data.days as Json[]).slice(-14).reverse();
  return (
    <section className="panel" aria-labelledby="cost-h">
      <h2 id="cost-h">費用レポート</h2>
      <dl className="kv">
        <dt>直近7日</dt><dd>${data.last_7_days_usd.toFixed(4)}</dd>
        <dt>今月（{data.month.month}）</dt><dd>${data.month.counted_usd.toFixed(4)}</dd>
        <dt>使える教材1件あたり</dt><dd>{data.avg_per_ready_unit_usd == null ? "—" : `$${data.avg_per_ready_unit_usd.toFixed(4)}`}（直近31日に完成した {data.ready_units} 件の平均）</dd>
        <dt>使えなかった教材の費用</dt><dd>${data.wasted_usd.toFixed(4)}（隔離・失敗 {data.wasted_jobs} 件）</dd>
        {data.in_progress_usd > 0 && <><dt>処理中・待機中の費用</dt><dd>${data.in_progress_usd.toFixed(4)}</dd></>}
      </dl>
      <details><summary className="btn ghost small">日ごとの利用（直近14日）</summary>
        <div className="table-wrap"><table className="list">
          <thead><tr><th scope="col">日付（UTC）</th><th scope="col">計上</th></tr></thead>
          <tbody>{days.map((d) => <tr key={d.day}><td>{d.day}</td><td>${d.counted_usd.toFixed(4)}</td></tr>)}</tbody>
        </table></div>
      </details>
      <p className="faint">{data.note}</p>
    </section>
  );
}
