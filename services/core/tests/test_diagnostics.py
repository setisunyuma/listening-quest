"""Self-check in 教材管理: free checks only, nothing secret in the output."""

from test_api import login

from lq.db import models as m
from lq.services import auth


def test_diagnostics_cover_db_storage_disk_and_worker_without_secrets(db, user):
    r = login().get("/api/admin/diagnostics")
    assert r.status_code == 200
    body = r.json()
    keys = {i["key"] for i in body["items"]}
    assert {"db_latency", "storage", "disk", "ffmpeg", "worker"} <= keys
    assert "api_key" not in keys  # demo realm: no paid providers
    assert next(i for i in body["items"] if i["key"] == "storage")["status"] == "ok"
    assert body["status"] in ("ok", "warn")


def test_live_diagnostics_report_the_key_only_as_set_or_not(db, monkeypatch):
    auth.create_user(db, "owner", "owner-password-123", realm="live")
    db.commit()
    from lq.config import get_settings

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-should-never-appear")
    get_settings.cache_clear()
    try:
        r = login("owner", "owner-password-123").get("/api/admin/diagnostics")
        assert "sk-test" not in r.text
        item = next(i for i in r.json()["items"] if i["key"] == "api_key")
        assert item["status"] == "ok" and item["detail"] == "サーバーに設定済み"
    finally:
        get_settings.cache_clear()
    assert db.query(m.User).count() == 1


def test_cleanup_removes_only_audio_of_unusable_material(db, user):
    from datetime import UTC, datetime, timedelta

    from helpers import ready_unit

    from lq.services import admin
    from lq.services import content as C
    from lq.storage import get_storage

    good = ready_unit(db, seed="keep1")
    bad = ready_unit(db, seed="drop1")
    C.quarantine(db, bad, "test")
    bad.created_at = datetime.now(UTC) - timedelta(days=30)
    db.commit()
    before = admin.storage_summary(db, "demo")
    assert before["unusable_bytes"] > 0 and before["usable_bytes"] > 0
    bad_keys = [a.storage_key for a in db.query(m.Asset).filter(m.Asset.unit_id == bad.id)]
    good_keys = [a.storage_key for a in db.query(m.Asset).filter(m.Asset.unit_id == good.id)]
    r = login().post("/api/admin/storage/cleanup")
    assert (
        r.status_code == 200
        and r.json()["deleted_files"] == len(bad_keys)
        and r.json()["freed_bytes"] == before["unusable_bytes"]
    )
    st = get_storage()
    assert not any(st.exists(k) for k in bad_keys) and all(st.exists(k) for k in good_keys)
    db.expire_all()
    assert db.get(m.Unit, bad.id) is not None  # the record stays; only sound files go
    assert admin.storage_summary(db, "demo")["unusable_bytes"] == 0
