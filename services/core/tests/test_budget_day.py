"""The budget day is the app's learning day: Japan time, switching at 04:00 (not the UTC calendar day)."""

from datetime import UTC, date, datetime

import pytest

from lq.db import models as m
from lq.generator import budget
from lq.generator.budget import BudgetBlocked, BudgetMeter


def test_budget_day_switches_at_four_in_japan():
    assert budget.budget_day(datetime(2026, 11, 14, 18, 59, tzinfo=UTC)) == date(2026, 11, 14)  # 03:59 JST on the 15th
    assert budget.budget_day(datetime(2026, 11, 14, 19, 0, tzinfo=UTC)) == date(2026, 11, 15)  # 04:00 JST
    assert budget.budget_day(datetime(2026, 11, 14, 1, 0, tzinfo=UTC)) == date(
        2026, 11, 14
    )  # 10:00 JST, UTC date the same
    start, end = budget.day_window(date(2026, 11, 15))
    assert (start, end) == (datetime(2026, 11, 14, 19, 0, tzinfo=UTC), datetime(2026, 11, 15, 19, 0, tzinfo=UTC))


def _entry(db, at, usd):
    db.add(m.CostEntry(realm="live", step="content", provider="openai", model="x", state="settled", budget_day=at.date(),
                       estimated_usd=usd, actual_usd=usd, usage={}, created_at=at))  # fmt: skip


def test_spend_is_counted_by_when_it_happened_including_older_utc_dated_rows(db):
    _entry(db, datetime(2026, 11, 14, 20, 0, tzinfo=UTC), 0.3)  # 05:00 JST on the 15th, stored with UTC date 14th
    _entry(db, datetime(2026, 11, 15, 9, 0, tzinfo=UTC), 0.2)  # 18:00 JST on the 15th
    _entry(db, datetime(2026, 11, 14, 18, 0, tzinfo=UTC), 0.5)  # 03:00 JST on the 15th = still the 14th's budget day
    db.commit()
    assert budget.spent_today(db, "live", date(2026, 11, 15))["counted_usd"] == pytest.approx(0.5)
    assert budget.spent_today(db, "live", date(2026, 11, 14))["counted_usd"] == pytest.approx(0.5)
    assert budget.spent_month(db, "live", date(2026, 11, 15))["counted_usd"] == pytest.approx(1.0)


def test_daily_cap_uses_the_japan_time_day(db, live_user, monkeypatch):
    budget.set_system(db, "live", {"paid_enabled": True, "daily_budget_usd": 1.0})
    db.commit()
    now = datetime.now(UTC)
    _entry(db, now, 0.95)
    db.commit()
    with pytest.raises(BudgetBlocked):
        BudgetMeter("live", None).reserve("content", "openai", "x", 0.1)
