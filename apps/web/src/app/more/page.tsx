"use client";
import Link from "next/link";

export default function MorePage() {
  return (
    <section className="panel" aria-label="メニュー">
      {[["/check", "本番条件の実力チェック"], ["/history", "履歴"], ["/settings", "設定"], ["/admin", "教材管理"]].map(([h, l]) => <p key={h}><Link className="btn wide big" href={h}>{l}</Link></p>)}
    </section>
  );
}
