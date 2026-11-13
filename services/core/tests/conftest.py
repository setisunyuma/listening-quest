import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="lq-test-")
os.environ["LQ_ENV"] = "test"
os.environ["LQ_ENV_FILE"] = "none"  # never read a developer's .env (real API keys) in tests
os.environ["LQ_DATABASE_URL"] = os.environ.get(
    "LQ_TEST_DATABASE_URL", "postgresql+psycopg://lq:lq@127.0.0.1:55432/lq_test"
)
os.environ["LQ_STORAGE_BACKEND"] = "local"
os.environ["LQ_STORAGE_LOCAL_DIR"] = f"{_TMP}/storage"
os.environ["LQ_OUTBOX_DIR"] = f"{_TMP}/outbox"
os.environ["LQ_IMPORT_TOKEN"] = "test-import-token"
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("LQ_SERVER_URL", None)

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from lq.db.base import Base, get_engine, session_factory  # noqa: E402
from lq.generator import factory  # noqa: E402
from lq.services import auth  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _schema():
    from alembic.config import Config

    from alembic import command

    eng = get_engine()
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    eng = get_engine()
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with eng.begin() as c:
        c.execute(text(f"TRUNCATE {names} CASCADE"))
    factory._override.clear()

    # No test may reach a paid API: any outbound HTTP / WebSocket attempt fails loudly.
    def _blocked(*a, **k):
        raise AssertionError("network call attempted in tests")

    import httpx
    import websockets.sync.client as wsc

    for name in ("post", "get", "request", "stream"):
        monkeypatch.setattr(httpx, name, _blocked)
    real_send = httpx.Client.send

    def _send(self, request, *a, **k):
        # FastAPI's TestClient is an httpx.Client talking to the in-process app ("testserver"): allowed.
        # Any other destination is the outside world and is refused.
        if request.url.host != "testserver":
            raise AssertionError(f"network call attempted in tests: {request.url}")
        return real_send(self, request, *a, **k)

    monkeypatch.setattr(httpx.Client, "send", _send)
    monkeypatch.setattr(wsc, "connect", _blocked)
    yield


@pytest.fixture
def db():
    s = session_factory()()
    yield s
    s.rollback()
    s.close()


@pytest.fixture
def user(db):
    u = auth.create_user(db, "demo", "demo-password-123", realm="demo")
    db.commit()
    return u


@pytest.fixture
def live_user(db):
    u = auth.create_user(db, "owner", "owner-password-123", realm="live")
    db.commit()
    return u
