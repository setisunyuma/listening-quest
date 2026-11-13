"use client";
import Link from "next/link";
import { Msg, useData } from "@/components/ui";
import { api, MODE_LABEL, type Json } from "@/lib/api";

const TYPE: Record<string, string> = { first: "初見", review: "復習", assisted: "補助付き", repeat: "既出の練習", partial: "一部未回答", assessment: "実力チェック" };
const AID: Record<string, string> = { extra_replay: "追加再生", speed: "速度変更", transcript: "英文表示", hint: "ヒント", prompt_text: "問いの文字表示" };
const REASON: Record<string, string> = { sound: "音が取れない", vocabulary: "語彙", numbers_negation: "数字・否定", inference: "推論", organizing: "情報整理", focus: "集中", other: "その他" };

export default function HistoryPage() {
  const { data, error } = useData<Json>(() => api("/history?limit=100"), []);
  if (!data) return error ? <Msg>{error}</Msg> : <p className="note" role="status">読み込み中…</p>;
  return (
    <>
      <section className="panel"><h2>解答の履歴</h2>
        {data.attempts.length === 0 ? <p className="note">まだ履歴がありません。</p> : (
          <div className="table-wrap"><table className="list">
            <thead><tr><th scope="col">日時</th><th scope="col">教材</th><th scope="col">結果</th><th scope="col">記録の種類と条件</th><th scope="col">自己申告</th><th scope="col"><span className="sr">詳細</span></th></tr></thead>
            <tbody>{data.attempts.map((a: Json) => (
              <tr key={a.attempt_id}>
                <td>{new Date(a.at).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}<br /><small>学習日 {a.learning_day}</small></td>
                <td>{a.title}<br /><small>{MODE_LABEL[a.mode]}</small>{a.invalidated && <><br /><span className="chip demo">無効化済み（集計から除外）</span><small> {a.invalidated_reason}</small></>}</td>
                <td>{a.correct_count} / {a.total}{!a.complete && <><br /><small>未回答あり</small></>}</td>
                <td><span className="chip">{TYPE[a.category] ?? a.category}</span>{a.counted_as_first && <span className="chip">初見成績に算入</span>}{a.presentation !== "official" && <span className="chip">アレンジ条件</span>}{a.offline && <span className="chip">オフライン解答</span>}
                  <br /><small>{[...a.aids.map((x: string) => AID[x] ?? x), ...a.conditions.filter((c: string) => !Object.values(AID).includes(c))].join("・")}</small></td>
                <td><small>{Object.values(a.self_reasons as Record<string, string>).map((r) => REASON[r] ?? r).join("・")}</small></td>
                <td><Link className="btn small" href={`/play?attempt=${a.attempt_id}`}>解説</Link></td>
              </tr>
            ))}</tbody></table></div>
        )}
      </section>
      <section className="panel"><h2>ラジオ後の自己申告</h2>
        {data.self_reports.length === 0 ? <p className="note">自己申告はまだありません。</p> : data.self_reports.map((r: Json) => <p key={r.id}>{r.at.slice(0, 10)}　{r.title} <span className="chip">{r.label}</span> <small>{MODE_LABEL[r.mode]}・客観採点とは別の記録</small></p>)}
      </section>
      <section className="panel"><h2>訂正・無効化の記録</h2>
        {data.corrections.filter((c: Json) => c.action === "unit_invalidated").length === 0 ? <p className="note">記録はありません。</p> : data.corrections.filter((c: Json) => c.action === "unit_invalidated").map((c: Json, i: number) => <p key={i}>{c.at.slice(0, 10)}　教材を無効化: {c.details.title} <small>理由: {c.details.reason}</small></p>)}
      </section>
    </>
  );
}
