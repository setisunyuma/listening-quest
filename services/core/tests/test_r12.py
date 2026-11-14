"""R12: an incomplete Realtime response that carries usage is costed from that usage; without usage it stays unknown."""

import json

import pytest
from sqlalchemy import select

from lq.db import models as m
from lq.generator import budget
from lq.generator.budget import BudgetMeter
from lq.generator.providers import ProviderError

USAGE = {
    "input_token_details": {"text_tokens": 100, "audio_tokens": 0},
    "output_token_details": {"text_tokens": 0, "audio_tokens": 400},
}


class FakeWS:
    def __init__(self, events):
        self.events = [json.dumps(e) for e in events]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send(self, _):
        pass

    def recv(self, timeout=None):
        return self.events.pop(0)


def _run(monkeypatch, events):
    import websockets.sync.client as wsc

    from lq.generator.openai_provider import RealtimeTTS

    monkeypatch.setattr(wsc, "connect", lambda *a, **k: FakeWS(events))
    meter = BudgetMeter("live", None)
    with pytest.raises(ProviderError):
        RealtimeTTS("sk-test", "wss://example.invalid/v1/realtime", "gpt-realtime-2.1-mini", 5).synthesize(
            "Hello there.", "cedar", "en", "", meter
        )


def test_incomplete_response_with_usage_is_settled_from_usage(db, live_user, monkeypatch):
    budget.set_system(db, "live", {"paid_enabled": True})
    db.commit()
    _run(
        monkeypatch,
        [
            {
                "type": "response.done",
                "response": {"status": "incomplete", "status_details": {"reason": "max_output_tokens"}, "usage": USAGE},
            }
        ],
    )
    e = db.execute(select(m.CostEntry)).scalars().one()
    assert e.state == "settled" and e.actual_usd == pytest.approx(budget.cost_realtime("gpt-realtime-2.1-mini", USAGE))
    assert e.usage["response_status"] == "incomplete"


def test_incomplete_response_without_usage_stays_unknown(db, live_user, monkeypatch):
    budget.set_system(db, "live", {"paid_enabled": True})
    db.commit()
    _run(monkeypatch, [{"type": "response.done", "response": {"status": "failed", "status_details": {}}}])
    e = db.execute(select(m.CostEntry)).scalars().one()
    assert e.state == "unknown" and e.actual_usd is None  # never zero
