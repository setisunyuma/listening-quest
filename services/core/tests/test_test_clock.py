"""The browser-test clock exists only when LQ_ENV=test; elsewhere the endpoint is refused."""

from lq.config import get_settings


def test_clock_endpoint_is_refused_outside_tests(db, user, monkeypatch):
    from test_api import login

    c = login()
    assert c.post("/api/test/clock", json={"offset_hours": 24}).status_code == 200
    assert c.post("/api/test/clock", json={"offset_hours": 0}).status_code == 200
    monkeypatch.setattr(get_settings(), "env", "prod")
    assert c.post("/api/test/clock", json={"offset_hours": 24}).status_code == 404
