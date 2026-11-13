"""Test helpers: quick READY units (tiny placeholder assets, no audio pipeline) and attempt drivers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from lq.audio.pcm import wav_bytes
from lq.db import models as m
from lq.domain.content import UnitContent, sha256_hex
from lq.domain.profiles import current_profile, part_spec
from lq.generator.demo import _Builder, _tone
from lq.generator.providers import GenSpec
from lq.services import content as C
from lq.services import learning as L
from lq.storage import get_storage

_WAV = wav_bytes(_tone(300, 400))  # real, decodable audio (0.4 s tone)
T0 = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)  # Mon 12:00 JST


def eid() -> str:
    return uuid.uuid4().hex


def make_content(mode="common_test", part="3", skill="explicit_detail", band=3, seed="a", **over) -> UnitContent:
    tmpl = part_spec(current_profile(mode), part)["template_type"]
    data = _Builder(GenSpec(mode=mode, part=part, template_type=tmpl, primary_skill=skill, band=band, seed=seed, source="test")).build()
    data.update(over)
    return UnitContent.model_validate(data)


def ready_unit(db, realm="demo", content: UnitContent | None = None, pool="practice", **kw) -> m.Unit:
    content = content or make_content(**kw)
    unit, _ = C.register_unit(db, realm, content, pool=pool)
    st = get_storage()
    roles = [("stimulus", f"stimulus:{s.id}") for s in content.segments] + [("exam", "exam"), ("feedback", "feedback"), ("radio", "radio")]
    roles += [("utterance", f"utt:{u.id}") for s in content.segments for u in s.utterances]
    for kind, role in roles:
        data = _WAV
        key = f"units/{unit.id}/{sha256_hex(role.encode())[:16]}.wav"
        st.put(key, data)
        db.add(m.Asset(unit_id=unit.id, kind=kind, role=role, storage_key=key, sha256=sha256_hex(data), bytes=len(data), duration_ms=20000, quality_state="decoded_ok"))
    db.flush()
    C.finalize_ready(db, unit, "mock_checked")
    unit.audio_kind = "demo_tone"
    db.commit()
    return unit


def answers(unit: m.Unit, correct=True, skip=()) -> dict:
    content = C.load_content(unit)
    out = {}
    for i, q in enumerate(content.sorted_questions()):
        ok = correct[i] if isinstance(correct, (list, tuple)) else correct
        if q.id in skip:
            out[q.id] = None
        elif ok:
            out[q.id] = {"choice_id": q.answer.choice_id}
        else:
            out[q.id] = {"choice_id": next(c.id for c in q.choices if c.id != q.answer.choice_id)}
    return out


def play_official(db, user, att: m.Attempt, extra: int = 0, rate: float = 1.0) -> None:
    content = C.load_content(db.get(m.Unit, att.unit_id))
    for seg in content.segments:
        for _ in range(seg.plays + extra):
            L.record_play(db, user, att.id, event_id=eid(), segment_id=seg.id, rate=rate)
    db.commit()


def do_attempt(db, user, unit: m.Unit, *, correct=True, now=T0, slot_id=None, extra_plays=0, rate=1.0, aid=None, ack=True, skip=()) -> m.Attempt:
    att = L.start_attempt(db, user, attempt_id=eid(), unit_id=unit.id, slot_id=slot_id, now=now)
    db.commit()
    play_official(db, user, att, extra_plays, rate)
    if aid:
        L.request_aid(db, user, att.id, event_id=eid(), kind=aid)
        db.commit()
    att = L.submit_attempt(db, user, att.id, answers(unit, correct, skip), now=now + timedelta(minutes=1))
    db.commit()
    if ack:
        L.ack_feedback(db, user, att.id, now=now + timedelta(minutes=2))
        db.commit()
    return att


def days(n: float) -> timedelta:
    return timedelta(days=n)
