"""Self-check shown in 教材管理: the things that broke or slowed the first deployment, checked in one place.

Every check is free (no paid API call) and reads nothing secret: the API key is reported only as set / not set."""

from __future__ import annotations

import os
import shutil
import time
from datetime import UTC, datetime

from sqlalchemy import func, select, text

from lq.config import get_settings
from lq.db import models as m
from lq.generator import budget
from lq.storage import get_storage

DB_SLOW_MS = 20.0  # one round trip; same-region databases answer in about 1 ms


def _item(key: str, label: str, ok: bool, detail: str, level: str | None = None) -> dict:
    return {"key": key, "label": label, "status": level or ("ok" if ok else "error"), "detail": detail}


def run(session, realm: str) -> dict:
    s = get_settings()
    items = []

    # database round trip (several samples; the median hides a single hiccup)
    samples = []
    for _ in range(5):
        t = time.perf_counter()
        session.execute(text("select 1"))
        samples.append((time.perf_counter() - t) * 1000)
    rt = sorted(samples)[len(samples) // 2]
    items.append(_item("db_latency", "データベースの応答", rt < DB_SLOW_MS,
                       f"往復 {rt:.1f} ms" + ("" if rt < DB_SLOW_MS else "。アプリと PostgreSQL が別の地域にある可能性があります（docs/DEPLOYMENT.md）"),
                       None if rt < DB_SLOW_MS else "warn"))  # fmt: skip

    # storage: write, read back, delete
    try:
        st = get_storage()
        key = f"diagnostics/probe-{os.getpid()}.txt"
        st.put(key, b"ok")
        ok = st.get(key) == b"ok"
        st.delete(key)
        items.append(
            _item("storage", "音声の保存先", ok, "書き込み・読み出しできます" if ok else "読み出した内容が一致しません")
        )
    except Exception as e:  # noqa: BLE001
        items.append(_item("storage", "音声の保存先", False, f"書き込めません: {type(e).__name__}"))
    if s.storage_backend == "local":
        try:
            du = shutil.disk_usage(s.storage_local_dir)
            free_gb = du.free / 1e9
            items.append(_item("disk", "保存先の空き容量", free_gb > 0.2, f"空き {free_gb:.2f} GB / 全体 {du.total / 1e9:.2f} GB",
                               None if free_gb > 0.5 else ("warn" if free_gb > 0.2 else "error")))  # fmt: skip
        except OSError as e:
            items.append(_item("disk", "保存先の空き容量", False, f"確認できません: {e}"))

    items.append(
        _item(
            "ffmpeg",
            "音声変換（FFmpeg）",
            bool(shutil.which("ffmpeg")),
            "利用できます" if shutil.which("ffmpeg") else "見つかりません",
        )
    )

    if realm != "demo":
        items.append(_item("api_key", "OpenAI の APIキー", bool(s.openai_api_key),
                           "サーバーに設定済み" if s.openai_api_key else "未設定（取り込みと手動承認は使えます）",
                           None if s.openai_api_key else "warn"))  # fmt: skip
        table = budget.price_table()
        tts_known = (
            s.tts_model in table["realtime"]
            if s.tts_provider == "openai_realtime"
            else s.tts_speech_model in table["speech"]
        )
        known = (
            s.content_model in table["text"]
            and s.review_model in table["text"]
            and tts_known
            and s.asr_model in table["transcription"]
        )
        items.append(_item("prices", "モデルと単価表", known,
                           f"{s.content_model} / {s.tts_model if s.tts_provider != 'openai_speech' else s.tts_speech_model} / {s.asr_model}（単価確認日 {table['checked_at']}）"
                           + ("" if known else "。単価が分からないモデルは有料処理を始めません")))  # fmt: skip
        cfg = budget.get_system(session, realm)
        items.append(_item("paid", "有料生成", True,
                           ("有効" if cfg["paid_enabled"] else "無効") + f"・1日 ${cfg['daily_budget_usd']}・月 ${cfg.get('monthly_budget_usd')}"
                           + ("・自動補充あり" if cfg["auto_replenish"] else ""), "ok" if cfg["paid_enabled"] else "info"))  # fmt: skip

    # worker: something must be picking up jobs
    J = m.GenerationJob
    waiting = session.execute(
        select(func.count()).select_from(J).where(J.realm == realm, J.status == "QUEUED", J.run_after < func.now() - text("interval '10 minutes'"))
    ).scalar_one()  # fmt: skip
    items.append(_item("worker", "生成の処理（worker）", waiting == 0,
                       "待ち行列は流れています" if waiting == 0 else f"{waiting} 件が10分以上待っています。worker が止まっている可能性があります",
                       None if waiting == 0 else "warn"))  # fmt: skip

    version = os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("LQ_VERSION") or ""
    worst = (
        "error"
        if any(i["status"] == "error" for i in items)
        else ("warn" if any(i["status"] == "warn" for i in items) else "ok")
    )
    return {"status": worst, "items": items, "version": version[:12] or None, "region": os.environ.get("RAILWAY_REPLICA_REGION") or None,
            "checked_at": datetime.now(UTC).isoformat()}  # fmt: skip
