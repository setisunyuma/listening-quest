"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useApp } from "@/components/Shell";
import { BandChip, DemoChip, Msg, Ring, useData } from "@/components/ui";
import { api, ApiError, CATEGORY_LABEL, fmtMin, MODE_LABEL, type Home, type Json, type Plan } from "@/lib/api";
import { downloadAudio, listPacks, putPack } from "@/lib/offline";

const NOTE: Record<string, string> = {
  no_ready_units: "このモードには、まだ学習できる教材がありません。「教材管理」で生成または取り込みをするか、別のモードを選んでください。",
  short_inventory: "学習できる教材が少ないため、今日の予定は目標時間より短くなっています。",
};

export default function HomePage() {
  const app = useApp();
  const router = useRouter();
  const { data, error, reload } = useData<Home>(() => api("/home"), [app.mode], 15000);
  const [msg, setMsg] = useState<{ kind: "ok" | "err" | "warn"; text: string } | null>(null);
  const [busy, setBusy] = useState("");
  const packs = useData(() => listPacks(app.me.user_id), [app.pending]);

  const act = async (key: string, fn: () => Promise<void>) => {
    setBusy(key); setMsg(null);
    try { await fn(); } catch (e) { setMsg({ kind: "err", text: (e as ApiError).detail || (e as Error).message }); }
    setBusy("");
    reload();
  };
  const start = (kind: "quest" | "radio") => act(kind, async () => {
    const p = await api<Plan>("/plans/today", { body: { kind } });
    if (kind === "radio" && p.radio_program_id) router.push(`/radio?program=${p.radio_program_id}`);
  });
  const rebuild = (plan: Plan) => act("rebuild", async () => {
    const p = await api<Plan>(`/plans/${plan.id}/rebuild`, { body: { mode: app.mode } });
    setMsg({ kind: "ok", text: `残りを${MODE_LABEL[app.mode]}に変更しました。新しい予定: 全${p.total_slots}枠・${fmtMin(p.planned_sec)}（完了分を含む）` });
  });
  const takeOffline = (plan: Plan) => act("offline", async () => {
    const { slots } = await api<{ slots: Json[] }>(`/plans/${plan.id}/reserve`, { body: {} });
    let n = 0;
    for (const s of slots) {
      for (const seg of s.unit.segments) if (seg.audio) await downloadAudio(app.me.user_id, seg.audio.url, seg.audio);
      await putPack(app.me.user_id, { ...s, saved_at: new Date().toISOString() });
      n++;
    }
    packs.reload();
    setMsg({ kind: "ok", text: `${n}件の問題と音声を端末に保存しました。オフラインでも解答できます（採点は同期後）。` });
  });
  const release = (plan: Plan) => act("release", async () => {
    if (app.pending > 0) { await app.syncNow(); }
    const r = await api<{ released: number }>(`/plans/${plan.id}/release`, { body: {} });
    setMsg({ kind: "ok", text: `オフライン用の予約を${r.released}件解除しました。` });
  });

  if (!data) return error ? <OfflineHome error={error} /> : <p className="note" role="status">読み込み中…</p>;
  const plan = data.plan;
  const next = plan?.slots.find((s) => s.state !== "completed");
  const slotHref = (s: Json) => `/play?unit=${s.unit.id}&slot=${s.id}`;
  return (
    <>
      {msg && <Msg kind={msg.kind}>{msg.text}</Msg>}
      {app.lastSync.length > 0 && (
        <div className="msg ok" role="status">オフラインの回答 {app.lastSync.length} 件を採点しました。
          {app.lastSync.filter((r) => r.attempt_id || r.status === "already_synced").slice(0, 3).map((r) => (<Link key={r.event_id} className="btn small" href={`/play?attempt=${r.event_id}`}>結果と解説を見る（{r.correct_count}/{r.total}）</Link>))}
        </div>
      )}
      <section className="panel accent" aria-labelledby="quest-h">
        <h2 id="quest-h">今日のクエスト <small>{MODE_LABEL[plan?.mode ?? app.mode]}・学習日 {data.today}</small></h2>
        {plan ? (
          <>
            <div className="quest">
              <Ring done={plan.completed_slots} total={plan.total_slots} />
              <div>
                <p>残り <strong>{plan.total_slots - plan.completed_slots}</strong> Unit ・ 予定 {fmtMin(plan.remaining_sec)} <small>（全体 {fmtMin(plan.planned_sec)} / 目標 {plan.goal_minutes}分）</small></p>
                {plan.status === "completed" ? <p className="verdict ok">✓ 今日の計画を完了しました</p> : plan.kind === "radio" ? (
                  <Link className="btn primary big" href={`/radio?program=${plan.radio_program_id}`}>ラジオを再生する</Link>
                ) : next ? (
                  <Link className="btn primary big" href={slotHref(next)}>{plan.completed_slots || next.state === "started" ? "再開する" : "開始する"}</Link>
                ) : null}
                <p className="faint">予定時間は音声の長さ・規定の再生回数・解答と解説の見込みからの推定で、ちょうどその時間で終わる保証ではありません。</p>
              </div>
            </div>
            {plan.notes.map((n) => <Msg key={n} kind="warn">{NOTE[n] ?? n}</Msg>)}
            {plan.kind === "quest" && plan.slots.map((s) => (
              <div key={s.id} className={`slot ${s.state}`}>
                <span className="n" aria-hidden="true">{s.state === "completed" ? "✓" : s.idx + 1}</span>
                <div>
                  <div className="row"><span className={`chip cat-${s.category}`}>{CATEGORY_LABEL[s.category]}</span>{s.unit && <BandChip band={s.unit.band} />}{s.reserved && <span className="chip">オフライン用に予約</span>}{s.unit && <DemoChip kind={s.unit.audio_kind} />}</div>
                  <div>{s.unit?.part_label} <small>{s.reason_text}・{fmtMin(s.est_sec)}</small></div>
                  {s.needs_all_answers && <small>未回答があるため未完了です。続きから解くか、別の問題に替えられます。</small>}
                </div>
                {s.state === "completed" ? <span className="chip">完了</span> : s.unit && <Link className="btn small" href={slotHref(s)} aria-label={`${s.idx + 1}番目を${s.state === "started" ? "続ける" : "解く"}`}>{s.awaiting_feedback_ack ? "解説へ" : s.state === "started" ? "続ける" : "解く"}</Link>}
              </div>
            ))}
            {plan.kind === "quest" && plan.status === "active" && (
              <div className="row" style={{ marginTop: 12 }}>
                {plan.mode !== app.mode && <button onClick={() => rebuild(plan)} disabled={!!busy}>残りを{MODE_LABEL[app.mode]}に変更</button>}
                <button onClick={() => takeOffline(plan)} disabled={!!busy || !app.online}>{busy === "offline" ? "保存中…" : "未開始分をオフライン用に保存"}</button>
                {plan.slots.some((s) => s.reserved && s.state === "pending") && <button onClick={() => release(plan)} disabled={!!busy || !app.online}>オフライン用の予約を解除</button>}
              </div>
            )}
          </>
        ) : (
          <>
            {data.preview && data.preview.slots > 0 ? (
              <>
                <p>目標 {data.preview.goal_minutes}分 → <strong>{data.preview.slots}</strong> Unit・{fmtMin(data.preview.planned_sec)} の計画を作れます。</p>
                <p className="note">内訳: 復習 {data.preview.by_category.review ?? 0}・弱点練習 {data.preview.by_category.weak ?? 0}・範囲を広げる {data.preview.by_category.explore ?? 0} <small>（開始すると枠数と時間上限が固定されます）</small></p>
                <div className="row actions">
                  <button className="primary big" onClick={() => start("quest")} disabled={!!busy}>クエストを開始</button>
                  <button onClick={() => start("radio")} disabled={!!busy}>ラジオ中心で開始</button>
                </div>
                <p className="faint">ラジオ中心の計画は再生の進み具合で達成を判定し、解答型の成績とは別に記録します。</p>
              </>
            ) : null}
            {data.preview?.notes.map((n: string) => <Msg key={n} kind="warn">{NOTE[n] ?? n}</Msg>)}
          </>
        )}
        {data.carried_plan && (
          <div className="msg warn">前の学習日（{data.carried_plan.learning_day}）の計画が {data.carried_plan.total_slots - data.carried_plan.completed_slots} Unit 残っています。続けた分はその日の記録になります。
            {data.carried_plan.slots.filter((s) => s.state !== "completed" && s.unit).slice(0, 1).map((s) => <Link key={s.id} className="btn small" href={slotHref(s)}>続きから</Link>)}</div>
        )}
      </section>

      <section className="panel" aria-label="継続の記録">
        <div className="stats">
          <div><div className="v">{data.streak_days}</div><div className="k">連続学習日数</div></div>
          <div><div className="v">Lv {data.xp.level}</div><div className="k">レベル</div></div>
          <div><div className="v">{data.xp.total}</div><div className="k">XP（今日 +{data.xp.today}）</div></div>
        </div>
        <div className="bar" style={{ marginTop: 10 }} aria-hidden="true"><i style={{ width: `${(data.xp.into_level / data.xp.level_size) * 100}%` }} /></div>
        <p className="faint">XP・レベル・連続日数は学習の継続を示す指標です。能力の推定や試験の得点ではありません。</p>
      </section>

      <div className="grid2">
        <section className="panel" aria-labelledby="check-h">
          <h2 id="check-h">本番条件の{data.exam_label}</h2>
          <p className="note">未接触の教材だけを使い、途中の解説や補助なしで解きます。日次の計画とは別枠です。</p>
          {data.assessments.length ? data.assessments.slice(0, 1).map((a: Json) => (<p key={a.id}>{a.label}：{a.slots}問・所要 {fmtMin(a.duration_sec)}</p>)) : <p className="note">いま開始できるセットはありません（教材管理で作成できます）。</p>}
          <Link className="btn" href="/check">実力チェックへ</Link>
        </section>
        <section className="panel" aria-labelledby="recent-h">
          <h2 id="recent-h">最近の成果</h2>
          {data.recent.length ? data.recent.map((r: Json, i: number) => (
            <p key={i}><span className={`chip ${r.kind === "perfect_first" ? "rainbow" : "purple"}`}>{r.kind === "perfect_first" ? "初見全問正解" : "復習全問正解"}</span> {r.title} <small>{MODE_LABEL[r.mode]}</small></p>
          )) : <p className="note">全問正解した教材がここに並びます。</p>}
          <Link className="btn ghost small" href="/history">履歴を見る</Link>
        </section>
      </div>
      {(packs.data?.length ?? 0) > 0 && (
        <section className="panel" aria-labelledby="off-h">
          <h2 id="off-h">端末に保存した問題</h2>
          {packs.data!.map((p) => <p key={p.slot_id}>{p.card.part_label} <small>{fmtMin(p.est_sec)}</small> <Link className="btn small" href={`/play?unit=${p.unit_id}&slot=${p.slot_id}`}>解く</Link></p>)}
        </section>
      )}
    </>
  );
}

function OfflineHome({ error }: { error: string }) {
  const app = useApp();
  const packs = useData(() => listPacks(app.me.user_id), [app.pending]);
  return (
    <>
      <Msg kind="warn">{app.online ? error : "オフラインです。端末に保存した問題に解答できます。採点と解説は同期後に表示されます。"}</Msg>
      <section className="panel"><h2>端末に保存した問題</h2>
        {packs.data?.length ? packs.data.map((p) => <p key={p.slot_id}>{p.card.part_label} <small>{fmtMin(p.est_sec)}</small> <Link className="btn small" href={`/play?unit=${p.unit_id}&slot=${p.slot_id}`}>解く</Link></p>) : <p className="note">保存した問題はありません。</p>}
        {app.pending > 0 && <p className="note">解答済み・採点待ち: {app.pending}件</p>}
      </section>
    </>
  );
}
