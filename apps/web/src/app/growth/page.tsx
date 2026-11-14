"use client";
import Link from "next/link";
import { useState } from "react";
import { GoalPanel } from "@/components/Goals";
import { PracticePanel } from "@/components/Practice";
import { useApp } from "@/components/Shell";
import { Msg, useData } from "@/components/ui";
import { api, fmtMin, MODE_LABEL, type Json } from "@/lib/api";

function Stat({ s }: { s: Json }) {
  return (
    <div className="panel">
      <h3 style={{ marginTop: 0 }}>{s.label}</h3>
      {s.units === 0 ? <p className="note">データなし</p> : (
        <>
          <p><strong style={{ fontSize: "1.5rem" }}>{Math.round(s.unit_mean * 100)}%</strong> <small>Unit平均</small> {s.note && <span className="chip">{s.note}</span>}</p>
          <p className="note">母数: {s.units} Unit・{s.questions} 問中 {s.correct} 問正解</p>
        </>
      )}
    </div>
  );
}

const REASON_JA: Record<string, string> = { sound: "音が取れない", vocabulary: "語彙", numbers_negation: "数字・否定", inference: "推論", organizing: "情報整理", focus: "集中", other: "その他" };

export default function GrowthPage() {
  const app = useApp();
  const [days, setDays] = useState(30);
  const { data: g, error } = useData<Json>(() => api(`/stats/growth?mode=${app.mode}&days=${days}`), [app.mode, days]);
  const sched = useData<{ schedules: Json[] }>(() => api("/schedules"), []);
  if (!g) return error ? <Msg>{error}</Msg> : <p className="note" role="status">読み込み中…</p>;
  const due = (sched.data?.schedules ?? []).filter((s) => s.mode === app.mode);
  return (
    <>
      <section className="panel">
        <div className="row"><h2 style={{ margin: 0, flex: 1 }}>{g.mode_label} の記録</h2>
          <label className="field" style={{ margin: 0 }}>集計期間<select value={days} onChange={(e) => setDays(Number(e.target.value))}>{[7, 30, 90, 365].map((d) => <option key={d} value={d}>直近{d}日</option>)}</select></label></div>
        <p className="note">{g.period_from} 〜 {g.period_to}。種類の違う数字は混ぜずに表示しています。</p>
        <p className="faint">{g.disclaimer}</p>
      </section>
      <GoalPanel full />
      <PracticePanel full />
      <div className="grid2">
        <div className="panel"><h3 style={{ marginTop: 0 }}>{g.volume.label}</h3>
          <dl className="kv"><dt>学習したUnit</dt><dd>{g.volume.units}</dd><dt>回答数</dt><dd>{g.volume.answers}</dd><dt>ラジオの再生時間</dt><dd>{g.volume.radio_listened_sec ? fmtMin(g.volume.radio_listened_sec) : "0分"}</dd><dt>XP（全モード）</dt><dd>{g.volume.xp_all_modes}</dd></dl></div>
        <Stat s={g.first_official} /><Stat s={g.review} /><Stat s={g.assisted} />
        {g.first_arranged.units > 0 && <Stat s={g.first_arranged} />}
        {g.practice?.tasks > 0 && (
          <div className="panel"><h3 style={{ marginTop: 0 }}>{g.practice.label}</h3>
            <dl className="kv"><dt>実施した課題</dt><dd>{g.practice.tasks}</dd><dt>証拠になった課題</dt><dd>{g.practice.evidence_tasks}</dd>
              <dt>最初の理解（主要項目の平均）</dt><dd>{g.practice.first_mean === null ? "未確認" : `${Math.round(g.practice.first_mean * 100)}%`}</dd>
              {g.practice.after_check_mean !== null && <><dt>確認後の達成</dt><dd>{Math.round(g.practice.after_check_mean * 100)}%</dd></>}
              <dt>重大な取り違えがあった課題</dt><dd>{g.practice.critical}</dd></dl></div>
        )}
        <div className="panel"><h3 style={{ marginTop: 0 }}>{g.self_reported.label}</h3>
          <dl className="kv"><dt>自分では正解</dt><dd>{g.self_reported.counts.correct ?? 0}</dd><dt>自分では不正解</dt><dd>{g.self_reported.counts.incorrect ?? 0}</dd><dt>不明</dt><dd>{g.self_reported.counts.unknown ?? 0}</dd><dt>ラジオで接触したUnit</dt><dd>{g.self_reported.radio_units}</dd></dl></div>
      </div>
      <section className="panel" aria-labelledby="band-h">
        <h2 id="band-h">{g.band.label}: <span className={`chip b${g.band.current}`}>{g.band.current}</span></h2>
        <p className="note">現在の難度帯での適格な初見結果: {g.band.eligible_units_in_band} / {g.band.needed} Unit（異なる音声）。{g.band.needed}件そろうと、直近最大20件の平均が {Math.round(g.band.up_threshold * 100)}% 以上で1段階上げ、{Math.round(g.band.down_threshold * 100)}% 未満で1段階下げます（1学習日に最大1段階）。</p>
        <p className="faint">{g.band.note} 自己申告・ラジオ・補助付きの正解・同じ問題の解き直しは難度の変更に使いません。他のモードの難度は引き継ぎません。</p>
        {g.band.history.map((h: Json, i: number) => <p key={i} className="note">{h.day}: {h.from} → {h.to}（{h.n} Unit・平均 {Math.round(h.mean * 100)}%）</p>)}
      </section>
      {(g.next_focus?.length > 0 || Object.keys(g.error_reasons ?? {}).length > 0 || g.missed_lines > 0) && (
        <section className="panel accent" aria-labelledby="focus-h">
          <h2 id="focus-h">次に練習すること</h2>
          {g.next_focus.map((f: Json) => <p key={f.skill}><strong>{f.label}</strong><br /><small>{f.reason_ja}</small></p>)}
          {Object.keys(g.error_reasons).length > 0 && <p className="note">誤答の理由（自己申告）: {Object.entries(g.error_reasons as Record<string, number>).sort((a, b) => b[1] - a[1]).map(([r, n]) => `${REASON_JA[r] ?? r} ${n}`).join("・")}</p>}
          {g.missed_lines > 0 && <p><Link className="btn small" href="/missed">聞き取れなかった文 {g.missed_lines} 文を聞き直す</Link></p>}
          <p className="faint">今日のクエストは、直近に間違えた技能と、平均が低い技能を優先して教材を選びます。</p>
        </section>
      )}
      {g.repair && g.repair.lines + g.repair.phrases > 0 && (
        <section className="panel" aria-labelledby="rep-h">
          <h2 id="rep-h">修復の結果</h2>
          <dl className="kv">
            <dt>その場でできた</dt><dd>{g.repair.immediate_ok} / {g.repair.lines} 文</dd>
            <dt>後日も同じ文でできた（英文なし）</dt><dd>{g.repair.delayed_ok} 文</dd>
            <dt>別の初見音声で同じ技能ができた</dt><dd>{g.repair.transfer_ok} / {g.repair.transfer_checked} 文</dd>
            <dt>覚える語句</dt><dd>{g.repair.phrases} 件</dd>
          </dl>
          <p className="faint">{g.repair.note}</p>
        </section>
      )}
      <section className="panel" aria-labelledby="skill-h">
        <h2 id="skill-h">技能別（初見・直近最大{g.skills[0]?.window}件）</h2>
        <div className="table-wrap"><table className="list">
          <thead><tr><th scope="col">技能</th><th scope="col">状態</th><th scope="col">平均</th><th scope="col">母数</th><th scope="col">推移（古→新）</th></tr></thead>
          <tbody>{g.skills.map((s: Json) => (
            <tr key={s.skill}><th scope="row">{s.label}</th><td><span className="chip">{s.status_label}</span></td><td>{s.mean == null ? "—" : `${Math.round(s.mean * 100)}%`}</td><td>{s.n}</td>
              <td><span aria-label={s.trend.map((t: number) => `${Math.round(t * 100)}%`).join("、") || "データなし"}>{s.trend.map((t: number, i: number) => <span key={i} aria-hidden="true" title={`${Math.round(t * 100)}%`} style={{ display: "inline-block", width: 8, height: 4 + t * 20, marginRight: 2, background: t >= 0.85 ? "var(--ok)" : t >= 0.6 ? "var(--blue)" : "var(--err)", verticalAlign: "bottom", borderRadius: 2 }} />)}</span></td></tr>
          ))}</tbody></table></div>
        <p className="faint">{g.skills[0]?.min_observations}件未満の技能は「参考値（判定中）」で、弱点とは断定しません。</p>
      </section>
      {g.assessments.length > 0 && (
        <section className="panel"><h2>実力チェックの結果</h2>
          {g.assessments.map((a: Json) => <p key={a.session_id}>{a.at.slice(0, 10)}　{a.label}: {a.score_kind === "points" ? `${a.points} / ${a.max_points} 点` : `${a.correct_count} / ${a.slot_count} 問`}
            <small>　{Object.entries(a.by_section as Record<string, Json>).map(([k, v]) => `${k.startsWith("Part") ? k : app.mode === "common_test" ? `第${k}問` : k} ${a.score_kind === "points" ? `${v.points}/${v.max_points}` : `${v.correct}/${v.total}`}`).join("・")}</small></p>)}
        </section>
      )}
      <section className="panel"><h2>復習の予定 <small>{MODE_LABEL[app.mode]}</small></h2>
        {due.length === 0 ? <p className="note">予定はまだありません。学習した翌日に最初の復習が入ります。</p> : (
          <div className="table-wrap"><table className="list"><thead><tr><th scope="col">教材</th><th scope="col">次回</th><th scope="col">段階</th><th scope="col">直近の変更理由</th></tr></thead>
            <tbody>{due.slice(0, 40).map((s) => <tr key={s.unit_id + s.kind}><td>{s.title}{s.kind === "relisten" && <span className="chip">再聴</span>}</td><td>{s.due_day}{s.due && <span className="chip purple">期限到来</span>}</td><td>{s.stage + 1}（{s.interval_days}日間隔）</td><td>{s.last_change.reason_ja ?? ""}</td></tr>)}</tbody></table></div>
        )}
      </section>
      {g.excluded_invalidated > 0 && <p className="faint">無効化された教材の解答 {g.excluded_invalidated} 件は集計から除外しています（履歴には残っています）。</p>}
    </>
  );
}
