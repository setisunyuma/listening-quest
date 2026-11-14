#!/usr/bin/env bash
# Build a fresh, isolated data set for browser tests: its own database and storage directory, a demo
# user with demo material (free demo providers only), an empty live user, and one failed job.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export LQ_DATABASE_URL="${LQ_E2E_DATABASE_URL:-postgresql+psycopg://lq:lq@127.0.0.1:55432/lq_e2e}"
export LQ_STORAGE_BACKEND=local LQ_STORAGE_LOCAL_DIR="$ROOT/var/e2e-storage" LQ_ENV=test LQ_ENV_FILE=none
unset OPENAI_API_KEY || true
rm -rf "$ROOT/var/e2e-storage"
cd "$ROOT/services/core"
uv run python - <<'PY'
from sqlalchemy import text
from lq.db.base import get_engine
with get_engine().begin() as c:
    c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
PY
uv run lq migrate
E2E_DEMO_PW="demo-password-123" uv run lq demo --password-env E2E_DEMO_PW --mock-modes cefr_c1 >/dev/null
E2E_OWNER_PW="owner-password-123" uv run lq create-user owner --password-env E2E_OWNER_PW
uv run python - <<'PY'
# one generation that fails the blind review, so the management screen has a failure to show
from lq.db.base import session_scope
from lq.generator import factory
from lq.generator.demo import DemoASR, DemoContent, DemoReview, DemoTTS
from lq.jobs import queue, worker
from lq.services import seed

class Disagrees(DemoReview):
    def solve(self, payload, meter):
        out = super().solve(payload, meter)
        q = payload["questions"][0]
        out["answers"][q["id"]] = {"choice_id": next(c["id"] for c in q["choices"] if c["id"] != out["answers"][q["id"]]["choice_id"])}
        return out

factory.set_provider_override("demo", factory.Providers(DemoContent(), Disagrees(), DemoTTS(), DemoASR(), paid=False))
with session_scope() as s:
    queue.enqueue(s, "demo", "generate", {"mode": "common_test", "part": "3", "template_type": "short_dialogue", "primary_skill": "inference", "band": 3, "seed": "e2e-fail", "source": "demo-generated", "pool": "practice"}, dedupe_key="e2e:fail")
print(worker.drain("e2e"))
factory.set_provider_override("demo", None)
with session_scope() as s:
    print("mock sets:", seed.assemble_demo_mocks(s))
PY
echo "e2e data ready"
