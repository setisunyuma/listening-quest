"""Public-repository check: with paid generation ON (and auto restock ON), nobody but the logged-in owner can make
the owner pay. Fake paid providers count every call; no network is reachable in tests (conftest)."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_api import login
from test_pipeline import paid

from lq.api.app import app
from lq.config import get_settings
from lq.db import models as m
from lq.jobs import worker
from lq.services import auth, replenish

PAID_ON = dict(paid_enabled=True, auto_replenish=True, daily_budget_usd=50, per_job_cap_usd=5, monthly_budget_usd=500)
# the only routes that answer without a login: health, whether first-time setup is available, the API description
OPEN = {("GET", "/api/health"), ("GET", "/api/auth/setup"), ("GET", "/api/openapi.json"), ("GET", "/api/docs"),
        ("GET", "/api/docs/oauth2-redirect"), ("GET", "/api/redoc")}  # fmt: skip


def _routes():
    out = []
    for r in app.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api/"):
            continue
        for meth in sorted(getattr(r, "methods", set()) - {"HEAD", "OPTIONS"}):
            out.append((meth, path))
    return out


def _call(c, meth, path, **kw):
    url = re.sub(r"\{[^}]+\}", "x0000000000", path)
    if meth == "GET":
        return c.get(url, **kw)
    return c.request(meth, url, json={}, **kw)


def _jobs(db, realm=None):
    q = select(func.count()).select_from(m.GenerationJob)
    if realm:
        q = q.where(m.GenerationJob.realm == realm)
    db.expire_all()
    return db.execute(q).scalar_one()


def _costs(db):
    db.expire_all()
    return db.execute(select(func.count()).select_from(m.CostEntry)).scalar_one()


@pytest.fixture
def paid_owner(db, live_user):
    fake = paid(db, 0.01, **PAID_ON)
    return fake


def test_every_api_route_refuses_anonymous_callers_and_nothing_paid_starts(db, paid_owner):
    routes = _routes()
    assert len(routes) > 60  # all of them, not a sample
    anon = TestClient(app)
    for meth, path in routes:
        r = _call(anon, meth, path)
        if (meth, path) in OPEN:
            assert r.status_code == 200, (meth, path)
            continue
        if (meth, path) == ("POST", "/api/auth/login"):
            assert r.status_code == 422
            continue
        assert r.status_code in (401, 403, 404, 409, 422), (meth, path, r.status_code)
    # wrong passwords / setup codes, the machine import without (or with a wrong) token
    assert anon.post("/api/auth/login", json={"username": "owner", "password": "wrong-password"}).status_code == 401
    assert (
        anon.post("/api/auth/setup", json={"code": "x" * 16, "username": "intruder", "password": "y" * 16}).status_code
        == 409
    )
    files = {"manifest": ("m.json", b'{"units": []}', "application/json")}
    assert anon.post("/api/import", files=files).status_code == 401
    assert anon.post("/api/import", files=files, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert _jobs(db) == 0
    assert worker.drain("w") == []
    assert paid_owner.calls == [] and _costs(db) == 0


def test_owner_cookie_without_csrf_or_from_another_site_starts_nothing(db, paid_owner, monkeypatch):
    c = login("owner", "owner-password-123")
    no_csrf = TestClient(app, cookies=c.cookies)
    unsafe = [
        (meth, p) for meth, p in _routes() if meth != "GET" and not p.startswith("/api/auth/") and p != "/api/import"
    ]
    for meth, path in unsafe:
        assert _call(no_csrf, meth, path).status_code in (401, 403), (meth, path)
    wrong = TestClient(app, cookies=c.cookies, headers={"X-CSRF-Token": "not-the-token"})
    for path in ("/api/admin/replenish/run", "/api/admin/jobs", "/api/admin/mock/generate", "/api/admin/verify-live"):
        assert wrong.post(path, json={}).status_code == 403
    # a page on another site that somehow holds the token is still refused by the Origin check
    monkeypatch.setattr(get_settings(), "public_origin", "https://lq.example.com")
    r = c.post("/api/admin/replenish/run", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert _jobs(db) == 0 and worker.drain("w") == [] and paid_owner.calls == [] and _costs(db) == 0


def test_another_account_cannot_spend_the_owners_money(db, paid_owner):
    """A second account (here a demo-area account with a known password) uses everything it can reach -
    restock, manual generation, mock generation, practice, today's plan, live check. Only free demo work runs."""
    auth.create_user(db, "guest", "guest-password-123", realm="demo")
    db.commit()
    g = login("guest", "guest-password-123")
    g.post("/api/admin/replenish/run", json={})
    g.post("/api/admin/mock/generate", json={"mode": "common_test"})
    g.post("/api/admin/jobs", json={"mode": "common_test", "part": "3", "primary_skill": "numbers_negation",
                                    "band": 3, "count": 2, "pool": "practice"})  # fmt: skip
    g.post("/api/practice/P2A/start", json={})
    g.post("/api/plans", json={"minutes": 30})
    assert g.post("/api/admin/verify-live", json={}).status_code == 422  # live check is live-area only
    # the guest cannot change the owner's settings either: its PATCH only touches its own area
    g.patch("/api/admin/system", json={"paid_enabled": True, "daily_budget_usd": 999})
    assert _jobs(db, "live") == 0 and _jobs(db, "demo") > 0  # its requests worked - in its own area only
    worker.drain("w")
    assert paid_owner.calls == [] and _costs(db) == 0  # demo work uses free providers and records no cost


def test_background_restock_only_spends_for_the_owner_inside_the_owners_settings(db, paid_owner, live_user):
    auth.create_user(db, "guest", "guest-password-123", realm="demo")
    db.commit()
    # control: the owner's own restock is the one path that queues paid work (so the checks above can see it)
    n = replenish.run_all(db)
    db.commit()
    assert n > 0 and _jobs(db, "live") > 0
    # turning paid generation off stops the worker before any provider call
    from lq.generator import budget

    budget.set_system(db, "live", {"paid_enabled": False})
    db.commit()
    outs = worker.drain("w")
    assert "blocked:paid_disabled" in outs
    assert paid_owner.calls == [] and _costs(db) == 0  # nothing paid ran while paid generation was off
    db.expire_all()
    assert all(
        j.status == "BLOCKED"
        for j in db.execute(select(m.GenerationJob).where(m.GenerationJob.realm == "live")).scalars()
    )


# ---- every entry that can lead to paid work, each with VALID input (the owner's own call below proves it is valid)


def _fingerprint(db):
    """What a paid entry can change: live jobs (new, re-queued, released) and the live system settings."""
    from lq.generator import budget

    db.expire_all()
    jobs = db.execute(
        select(m.GenerationJob.id, m.GenerationJob.status, m.GenerationJob.attempts, m.GenerationJob.run_after).where(
            m.GenerationJob.realm == "live"
        )
    ).all()
    return sorted(map(tuple, jobs)), budget.get_system(db, "live")


def _paid_entries(db, monkeypatch):
    """Valid requests for every API entry that queues, re-queues or unblocks paid work."""
    import json
    from datetime import UTC, datetime, timedelta

    from helpers import make_content, ready_unit

    from lq.jobs import queue

    monkeypatch.setattr(get_settings(), "openai_api_key", "sk-test-not-a-key")  # a key is configured on the server
    monkeypatch.setattr("lq.generator.openai_provider.probe_auth", lambda *a, **k: [])  # the free probe goes online
    later = datetime.now(UTC) + timedelta(days=30)

    def parked(reason, status="BLOCKED", unit_id=None, seed="x"):
        j, _ = queue.enqueue(db, "live", "generate", {"mode": "common_test", "part": "3", "template_type": "short_dialogue",
                                                      "primary_skill": "numbers_negation", "band": 3, "seed": seed,
                                                      "source": "generated", "pool": "practice"})  # fmt: skip
        j.status, j.blocked_reason, j.run_after, j.unit_id = status, reason, later, unit_id
        return j.id

    retry_id = parked("budget", seed="g-retry")  # waits for the owner's retry
    release_id = parked("paid_disabled", seed="g-release")  # waits for a settings change
    unit = ready_unit(db, realm="live", seed="g-quarantine")
    unit.status, unit.status_reason = "QUARANTINED", "evidence does not support the answer"
    rewrite_id = parked(None, status="FAILED", unit_id=unit.id, seed="g-rewrite")
    db.commit()

    def manifest(seed):
        body = json.dumps({"schema_version": 2, **make_content(seed=seed).model_dump(mode="json")})
        return {"manifest": ("manifest.json", body, "application/json")}

    gen = {
        "mode": "common_test",
        "part": "3",
        "primary_skill": "numbers_negation",
        "band": 3,
        "count": 1,
        "pool": "practice",
    }
    entries = [
        ("POST", "/api/admin/jobs", {"json": gen}),
        ("POST", "/api/admin/mock/generate", {"json": {"mode": "common_test"}}),
        ("POST", "/api/admin/replenish/run", {"json": {}}),
        ("POST", "/api/admin/verify-live", {"json": {"cap_usd": 0.5}}),
        ("POST", f"/api/admin/jobs/{retry_id}/retry", {}),
        ("POST", f"/api/admin/jobs/{rewrite_id}/rewrite", {}),
        ("POST", "/api/admin/import", {"files": manifest("g-ui-import")}),
        ("PATCH", "/api/admin/system", {"json": {"paid_enabled": True, "daily_budget_usd": 100}}),  # releases release_id
    ]  # fmt: skip
    machine = ("POST", "/api/import", {"files": manifest("g-cli-import"), "data": {"realm": "live"}})
    return entries, machine, release_id


def _attempt_all(client, entries, machine, headers=None):
    out = [client.request(meth, path, headers=headers, **kw).status_code for meth, path, kw in entries]
    meth, path, kw = machine
    out.append(client.request(meth, path, headers=headers, **kw).status_code)
    return out


@pytest.mark.parametrize("paid_on", [True, False], ids=["paid-on", "paid-off-live-check-ready"])
def test_paid_entries_with_valid_input_are_refused_for_anyone_but_the_owner(db, paid_owner, monkeypatch, paid_on):
    entries, machine, _ = _paid_entries(db, monkeypatch)
    if not paid_on:  # the state in which the live check entry is valid
        from lq.generator import budget

        budget.set_system(db, "live", {"paid_enabled": False})
    auth.create_user(db, "guest", "guest-password-123", realm="demo")
    db.commit()
    before = _fingerprint(db)
    owner = login("owner", "owner-password-123")

    # 1 not logged in
    assert set(_attempt_all(TestClient(app), entries, machine)) <= {401}
    # 2 the owner's cookie without / with a wrong CSRF token (a page on another site cannot read the token)
    assert set(_attempt_all(TestClient(app, cookies=owner.cookies), entries, machine)) <= {401, 403}
    bad = TestClient(app, cookies=owner.cookies, headers={"X-CSRF-Token": "not-the-token"})
    assert set(_attempt_all(bad, entries, machine)) <= {401, 403}
    # 3 cookie and token, but sent from another site's page (Origin check of the public address)
    monkeypatch.setattr(get_settings(), "public_origin", "https://lq.example.com")
    assert set(_attempt_all(owner, entries, machine, headers={"Origin": "https://evil.example"})) <= {401, 403}
    monkeypatch.setattr(get_settings(), "public_origin", None)
    # 4 machine import with a wrong token
    meth, path, kw = machine
    assert owner.request(meth, path, headers={"Authorization": "Bearer wrong"}, **kw).status_code == 401
    # 5 another account, logged in properly: only its own (free demo) area is touched
    guest = login("guest", "guest-password-123")
    codes = _attempt_all(guest, entries, machine)
    assert codes[4] == codes[5] == 404 and codes[3] == 422 and codes[-1] == 401  # live jobs / live check / no token

    assert _fingerprint(db) == before  # no live job created, re-queued or released; live settings unchanged
    worker.drain("w")  # whatever the guest queued runs on the free demo providers
    assert paid_owner.calls == [] and _costs(db) == 0
    assert _fingerprint(db) == before


def test_control_the_same_valid_requests_from_the_owner_do_queue_paid_work(db, paid_owner, monkeypatch):
    """Proves the inputs above are valid and that the checks would see paid work: each owner call changes the
    live jobs or settings, and the worker then calls the (fake) paid providers."""
    entries, machine, release_id = _paid_entries(db, monkeypatch)
    owner = login("owner", "owner-password-123")
    for meth, path, kw in [*entries, (machine[0], machine[1], {**machine[2], "files": None})]:
        if path == "/api/import":
            kw = {**machine[2], "headers": {"Authorization": "Bearer test-import-token"}}
        if path == "/api/admin/verify-live":  # the one-unit live check starts only while paid generation is off
            from lq.generator import budget

            budget.set_system(db, "live", {"paid_enabled": False})
            db.commit()
        before = _fingerprint(db)
        r = owner.request(meth, path, **kw)
        assert r.status_code == 200, (path, r.text)
        assert _fingerprint(db) != before, path
    db.expire_all()
    assert db.get(m.GenerationJob, release_id).status == "QUEUED"
    worker.drain("w")
    assert paid_owner.calls and _costs(db) > 0
