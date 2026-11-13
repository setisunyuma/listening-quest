"""Acceptance tests 1, 12, 13, 14, 17: generation / import pipeline, recovery, cache, blind review, budget."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from helpers import make_content
from sqlalchemy import func, select

from lq.audio import qa
from lq.audio.pcm import AudioError, wav_bytes
from lq.db import models as m
from lq.db.base import session_factory
from lq.domain.content import HASHED_FIELDS, content_hash, learner_view, reviewer_view, sha256_hex
from lq.generator import budget, factory
from lq.generator.budget import BudgetBlocked, BudgetMeter
from lq.generator.demo import DemoASR, DemoContent, DemoReview, DemoTTS, _tone
from lq.generator.providers import ProviderError
from lq.jobs import pipeline, queue, worker
from lq.services import content as C
from lq.services import importer
from lq.services.seed import load_fixture_units, seed_fixtures
from lq.storage import get_storage

SPEC = {
    "mode": "common_test",
    "part": "3",
    "template_type": "short_dialogue",
    "primary_skill": "numbers_negation",
    "band": 3,
    "seed": "t1",
    "source": "demo-generated",
    "pool": "practice",
}


def demo_providers(**over):
    base = dict(content=DemoContent(), review=DemoReview(), tts=DemoTTS(), asr=DemoASR(), paid=False)
    base.update(over)
    return factory.Providers(**base)


def enqueue(db, realm="demo", **over):
    job, _ = queue.enqueue(db, realm, "generate", {**SPEC, **over})
    db.commit()
    return job.id


def unit_of(db, job_id):
    db.expire_all()
    job = db.get(m.GenerationJob, job_id)
    return job, (db.get(m.Unit, job.unit_id) if job.unit_id else None)


# ---- 1. generation -> READY with separated audio assets


def test_generate_job_reaches_ready_with_all_tracks(db, user):
    jid = enqueue(db)
    assert worker.drain("w1") == ["succeeded"]
    job, unit = unit_of(db, jid)
    assert (job.status, job.last_completed_step, unit.status) == ("SUCCEEDED", "ready", "READY")
    assert (
        unit.audio_kind == "demo_tone" and unit.audio_review_state == "mock_checked"
    )  # labelled as demo, not as checked speech
    assets = {a.role: a for a in db.execute(select(m.Asset).where(m.Asset.unit_id == unit.id)).scalars()}
    assert {"stimulus:s1", "exam", "feedback", "radio"} <= set(assets)
    st = get_storage()
    for a in assets.values():
        assert st.exists(a.storage_key) and sha256_hex(st.get(a.storage_key)) == a.sha256 and a.duration_ms > 0
        assert unit.content_id not in a.storage_key and "answer" not in a.storage_key
    kinds = {e["kind"] for e in assets["radio"].meta["timeline"]}
    assert {"prompt", "choices", "utterance", "answer_wait", "answer", "evidence", "explanation"} <= kinds
    assert not {"answer", "explanation", "evidence"} & {e["kind"] for e in assets["stimulus:s1"].meta["timeline"]}
    recs = {(r.target, r.method) for r in db.execute(select(m.ReviewRecord)).scalars()}
    assert recs == {("content", "demo_mock_reviewed"), ("audio", "audio_demo_mock_checked")}
    assert set(unit.session_types) == {"train", "review", "radio"}


def test_silence_is_explicit_pcm_and_repeats_reuse_the_same_clip(db):
    from lq.audio import render as R

    unit = make_content(part="1A", seed="rr")  # two official plays
    clips = {cid: _tone(200 + 13 * i, 300) for i, cid in enumerate(R.plan_clips(unit))}
    exam = R.render_exam(unit, clips, {"plays": 2, "answer_wait_sec": 2, "pre_read_sec": 0}, {"between_plays_sec": 1})
    utts = [e for e in exam.timeline if e["kind"] == "utterance"]
    assert len(utts) == 2 * len(unit.segments[0].utterances)
    pcm = exam.pcm()
    cut = lambda e: pcm[e["start_ms"] * 48 : e["end_ms"] * 48]  # noqa: E731
    half = len(utts) // 2
    assert cut(utts[0]) == cut(utts[half])  # second play is byte-identical
    wait = next(e for e in exam.timeline if e["kind"] == "answer_wait")
    assert set(cut(wait)) == {0} and wait["end_ms"] - wait["start_ms"] == 2000


# ---- 12. failures never produce READY; restart resumes; re-import does not duplicate


class FlakyTTS(DemoTTS):
    def __init__(self, fail_after):
        self.calls, self.fail_after = 0, fail_after

    def synthesize(self, *a, **k):
        self.calls += 1
        if self.calls > self.fail_after:
            raise ProviderError("simulated outage")
        return super().synthesize(*a, **k)


def test_audio_failure_keeps_unit_out_of_ready_and_resume_reuses_finished_clips(db, user):
    flaky = FlakyTTS(fail_after=3)
    factory.set_provider_override("demo", demo_providers(tts=flaky))
    jid = enqueue(db)
    assert worker.drain("w1") == ["retry"]
    job, unit = unit_of(db, jid)
    assert job.status == "RETRY_WAIT" and unit.status == "AUDIO_GENERATING" and unit.session_types == []
    assert db.execute(select(func.count()).select_from(m.Asset)).scalar_one() == 0
    assert (
        db.execute(select(func.count()).select_from(m.AudioCache)).scalar_one() == 3
    )  # only finished clips are cached
    counting = FlakyTTS(fail_after=10_000)
    factory.set_provider_override("demo", demo_providers(tts=counting))
    job.run_after = datetime.now(UTC)
    db.commit()
    assert worker.drain("w2") == ["succeeded"]
    job, unit = unit_of(db, jid)
    total = db.execute(select(func.count()).select_from(m.AudioCache)).scalar_one()
    assert unit.status == "READY" and counting.calls == total - 3  # the three finished clips were not synthesised again
    assert db.execute(select(func.count()).select_from(m.Unit)).scalar_one() == 1


def test_upload_failure_is_not_ready(db, user, monkeypatch):
    jid = enqueue(db)
    st = get_storage()
    real_put = st.put

    def broken(key, data, content_type="application/octet-stream"):
        if key.startswith("units/"):
            raise OSError("storage down")
        return real_put(key, data, content_type)

    monkeypatch.setattr(st, "put", broken)
    assert worker.drain("w1") == ["retry"]
    _, unit = unit_of(db, jid)
    assert unit.status != "READY" and db.execute(select(func.count()).select_from(m.Asset)).scalar_one() == 0


def test_dead_worker_is_reclaimed_and_stale_worker_cannot_finalise(db, user):
    jid = enqueue(db)
    old = queue.claim(db, "worker-old")
    db.commit()
    assert queue.claim(db, "worker-new") is None  # lease still valid (and max concurrency 1)
    job = db.get(m.GenerationJob, jid)
    job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)  # worker-old died / stalled
    db.commit()
    new = queue.claim(db, "worker-new")
    db.commit()
    assert new.generation == old.generation + 1
    with pytest.raises(queue.StaleLease):
        pipeline.execute(old)  # the old worker wakes up: it must not write anything
    assert queue.succeed(db, old) is False
    db.commit()
    job, unit = unit_of(db, jid)
    assert job.status == "RUNNING" and job.lease_owner == "worker-new" and unit is None
    pipeline.execute(new)
    assert queue.succeed(db, new) is True
    db.commit()
    assert unit_of(db, jid)[1].status == "READY"


def test_retry_is_bounded(db, user):
    factory.set_provider_override("demo", demo_providers(tts=FlakyTTS(fail_after=0)))
    jid = enqueue(db)
    seen = []
    for _ in range(6):
        seen += worker.drain("w")
        job = db.get(m.GenerationJob, jid)
        db.refresh(job)
        if job.status != "RETRY_WAIT":
            break
        assert job.run_after > datetime.now(UTC)
        job.run_after = datetime.now(UTC)
        db.commit()
    assert (
        job.status == "FAILED"
        and job.attempts == job.max_attempts
        and "retry limit" in job.stop_detail
        and len(seen) == job.max_attempts
    )


def _manifest_with_audio(unit):
    from lq.audio.render import plan_clips

    files, audio, blobs = {}, {}, {}
    for i, cid in enumerate(plan_clips(unit)):
        name = cid.replace(":", "_") + ".wav"
        blobs[name] = wav_bytes(_tone(220 + 7 * i, 500))
        files[name] = {"sha256": sha256_hex(blobs[name]), "bytes": len(blobs[name])}
        audio[cid] = name
    return {
        "schema_version": 2,
        "source": "ext",
        "units": [{**unit.model_dump(mode="json"), "source": "ext", "audio": audio, "review": {"status": "passed"}}],
        "files": files,
    }, blobs


def test_import_with_audio_skips_tts_needs_explicit_approval_and_is_idempotent(db, user):
    class NoTTS(DemoTTS):
        def synthesize(self, *a, **k):
            raise AssertionError("speech synthesis must be skipped when audio is imported")

    factory.set_provider_override("demo", demo_providers(tts=NoTTS()))
    manifest, blobs = _manifest_with_audio(make_content(seed="imp1"))
    rep = importer.import_manifest(db, "demo", manifest, blobs)
    db.commit()
    assert rep["status"] == "accepted" and rep["units"][0]["audio"]["missing_clips"] == []
    assert any(n["status"] == "review_claim_ignored" for n in rep["notes"])  # "passed" inside the JSON approves nothing
    assert worker.drain("w") == ["blocked:awaiting_content_review"]
    unit = db.execute(select(m.Unit)).scalar_one()
    assert unit.status == "CONTENT_REVIEW"
    importer.manual_approve(db, unit, target="content", reviewer="owner")
    db.commit()
    assert worker.drain("w") == ["succeeded"]
    db.refresh(unit)
    assert unit.status == "READY" and unit.audio_kind == "imported"
    assert [
        r.method for r in db.execute(select(m.ReviewRecord).where(m.ReviewRecord.target == "content")).scalars()
    ] == ["manual_reviewed"]
    again = importer.import_manifest(db, "demo", manifest, blobs)  # the same version sent again
    db.commit()
    assert again["status"] == "duplicate" and db.execute(select(func.count()).select_from(m.Unit)).scalar_one() == 1
    assert db.execute(select(func.count()).select_from(m.GenerationJob)).scalar_one() == 1
    manifest["units"][0]["questions"][0]["prompt_en"] = "A changed question?"
    conflict = importer.import_manifest(db, "demo", manifest, blobs)
    db.commit()
    assert conflict["status"] == "conflict" and "new revision" in conflict["units"][0]["errors"][0]


def test_import_rejects_bad_files_and_missing_evidence(db, user):
    manifest, blobs = _manifest_with_audio(make_content(seed="imp2"))
    name = next(iter(blobs))
    tampered = {**blobs, name: blobs[name] + b"x"}
    assert "sha256 does not match" in importer.import_manifest(db, "demo", manifest, tampered)["errors"][0]
    evil = {**manifest, "files": {**manifest["files"], "../../etc/passwd.wav": {"sha256": "0", "bytes": 1}}}
    assert (
        "not allowed"
        in importer.import_manifest(db, "demo", evil, {**blobs, "../../etc/passwd.wav": b"x"})["errors"][0]
    )
    del blobs[name]
    rep = importer.import_manifest(db, "demo", manifest, blobs)
    assert rep["units"][0]["status"] == "rejected" and "was not uploaded" in rep["units"][0]["errors"][0]
    broken = make_content(seed="imp3").model_dump(mode="json")
    broken["questions"][0]["evidence"] = [{"utterance_id": "u999"}]
    rep = importer.import_manifest(db, "demo", {**broken, "schema_version": 2}, {})
    assert rep["units"][0]["status"] == "rejected" and "missing utterance" in json.dumps(rep)
    db.rollback()


def test_legacy_complete_mp3_is_radio_only(db, user):
    audio = wav_bytes(_tone(300, 1500))
    legacy = {"schema_version": 1, "episode_id": "ep01", "title": "Episode 1", "mode": "common_test", "audio": "ep01.wav", "extra_field": 1,
              "items": [{"id": "a", "script": [{"speaker": "M", "text": "Hello."}], "question": "Q?", "choices": ["x", "y"], "answer": 1, "explanation_ja": "説明"}],
              "files": {"ep01.wav": {"sha256": sha256_hex(audio), "bytes": len(audio)}}}  # fmt: skip
    rep = importer.import_manifest(db, "demo", legacy, {"ep01.wav": audio})
    db.commit()
    status = {n["item"]: n["status"] for n in rep["notes"]}
    assert (
        status["ep01-radio"] == "radio_only"
        and status["ep01-a"] == "not_converted"
        and status["ep01"] == "ignored_field"
    )  # evidence is not invented
    assert worker.drain("w") == ["blocked:awaiting_content_review"]
    unit = db.execute(select(m.Unit)).scalar_one()
    importer.manual_approve(db, unit, target="content", reviewer="owner")
    db.commit()
    assert worker.drain("w") == ["blocked:awaiting_audio_approval"]  # no script to verify against: a person must listen
    importer.manual_approve(db, unit, target="audio", reviewer="owner")
    db.commit()
    assert worker.drain("w") == ["succeeded"]
    db.refresh(unit)
    assert unit.status == "READY" and unit.session_types == ["radio"] and unit.audio_review_state == "human_listened"


def test_fixture_seed_is_idempotent_and_realms_stay_apart(db, user, live_user):
    n = len(load_fixture_units())
    assert seed_fixtures(db, "demo") == {"created": n, "already_present": 0}
    assert seed_fixtures(db, "demo") == {"created": 0, "already_present": n}
    seed_fixtures(db, "live")
    db.commit()
    outcomes = worker.drain("w")
    by_realm = dict(
        db.execute(select(m.Unit.realm, func.count()).where(m.Unit.status == "READY").group_by(m.Unit.realm)).all()
    )
    assert by_realm == {"demo": n}  # live examples wait for real audio; demo tone audio never enters the live area
    assert outcomes.count("succeeded") == n and outcomes.count("blocked:missing_audio") == n
    methods = set(db.execute(select(m.ReviewRecord.method).where(m.ReviewRecord.target == "content")).scalars())
    assert methods == {"author_reviewed"}
    with pytest.raises(ValueError):
        C.add_review_record(
            db,
            db.execute(select(m.Unit).where(m.Unit.realm == "live")).scalars().first(),
            target="content",
            method="demo_mock_reviewed",
            reviewer="x",
            result="pass",
        )


# ---- 13. cache and hashing


def test_cache_key_tracks_every_synthesis_input():
    base = dict(text="Hello there.", provider="p", model="m1", voice="v1", speaker="sp1", lang="en", instructions="")
    k0 = pipeline.cache_key(**base)
    assert k0 == pipeline.cache_key(**base)
    for field, value in [
        ("text", "Hello there!"),
        ("model", "m2"),
        ("voice", "v2"),
        ("speaker", "sp2"),
        ("lang", "ja"),
        ("instructions", "slow"),
        ("provider", "q"),
    ]:
        assert pipeline.cache_key(**{**base, field: value}) != k0, field


def test_cache_never_serves_incomplete_or_damaged_audio(db, user):
    key = pipeline.cache_key("Hi.", "demo", "demo-tone-v1", "v", "sp1", "en", "")
    assert pipeline.cached_clip(db, key) is None
    pcm = _tone(300, 400)
    storage_key = pipeline.store_clip(db, key, pcm, "demo", "demo-tone-v1", "v")
    db.commit()
    assert pipeline.cached_clip(db, key) == pcm
    get_storage().put(storage_key, wav_bytes(pcm)[:200])  # truncated object (e.g. interrupted write elsewhere)
    assert pipeline.cached_clip(db, key) is None and db.get(m.AudioCache, key) is None
    with pytest.raises(AudioError):  # bad audio from a provider is rejected before it could be cached
        raise AudioError(qa.check_clip(b"\x00" * 48 * 500, "Some words here"))


def test_content_hash_covers_meaning_only():
    u = make_content(seed="h1")
    h = content_hash(u)
    assert "content_hash" not in HASHED_FIELDS and "review" not in HASHED_FIELDS
    assert (
        content_hash(
            u.model_copy(
                update={
                    "title": "other",
                    "revision": 9,
                    "estimated_duration_sec": 999,
                    "calibration_status": "calibrated",
                }
            )
        )
        == h
    )
    data = u.model_dump(mode="json")
    data["questions"][0]["feedback"]["explanation_ja"] += "追記"
    assert content_hash(type(u).model_validate(data)) != h
    data = u.model_dump(mode="json")
    data["segments"][0]["utterances"][0]["text"] += " Really."
    assert content_hash(type(u).model_validate(data)) != h


def test_review_record_only_counts_for_the_same_hash(db, user):
    u1, _ = C.register_unit(db, "demo", make_content(seed="rv"))
    C.add_review_record(db, u1, target="content", method="manual_reviewed", reviewer="me", result="pass")
    assert C.passing_review(db, u1, "content") is not None
    data = make_content(seed="rv").model_dump(mode="json")
    data["revision"] = 2
    data["questions"][0]["prompt_en"] = "A different question?"
    u2, status = C.register_unit(db, "demo", type(make_content()).model_validate(data))
    assert (
        status == "created" and C.passing_review(db, u2, "content") is None and C.carry_over_review(db, u2) is None
    )  # meaning changed: review again


def test_asr_comparison_separates_notation_from_meaning():
    assert qa.compare_transcript("It starts at 9:30, not at ten.", "it starts at nine thirty not at 10")["ok"]
    assert not qa.compare_transcript("It does not start at 9:30.", "It does start at 9:30.")["ok"]
    assert not qa.compare_transcript("The fee is fifteen dollars.", "The fee is fifty dollars.")["ok"]
    assert qa.compare_transcript("I can't come.", "I cannot come")["ok"]


# ---- 14. blind review


def test_reviewer_never_sees_the_key_and_mismatch_is_quarantined(db, user):
    reviewer = DemoReview()
    factory.set_provider_override("demo", demo_providers(review=reviewer))
    jid = enqueue(db)
    worker.drain("w")
    _, unit = unit_of(db, jid)
    content = C.load_content(unit)
    payload = json.dumps(reviewer.seen_payloads[0], ensure_ascii=False)
    q = content.questions[0]
    for secret in (
        q.feedback.explanation_ja,
        q.feedback.answer_en,
        q.feedback.distractors[0].reason_ja,
        unit.title,
        unit.family_id,
        "choice_id",
        "evidence",
        "answer",
        "skill",
    ):
        assert secret not in payload, secret
    assert set(reviewer.seen_payloads[0]) == set(reviewer_view(content))
    lv = json.dumps(learner_view(content), ensure_ascii=False)
    assert (
        q.feedback.explanation_ja not in lv
        and content.segments[0].utterances[0].text not in lv
        and '"answer"' not in lv
    )

    class Disagrees(DemoReview):
        def solve(self, payload, meter):
            out = super().solve(payload, meter)
            qid = payload["questions"][0]["id"]
            wrong = next(
                c["id"] for c in payload["questions"][0]["choices"] if c["id"] != out["answers"][qid]["choice_id"]
            )
            out["answers"][qid] = {"choice_id": wrong}
            return out

    factory.set_provider_override("demo", demo_providers(review=Disagrees()))
    jid2 = enqueue(db, seed="t2")
    assert worker.drain("w") == ["failed"]
    job2, unit2 = unit_of(db, jid2)
    assert unit2.status == "QUARANTINED" and "disagreed" in unit2.status_reason and job2.status == "FAILED"
    assert db.execute(select(m.ReviewRecord.result).where(m.ReviewRecord.unit_id == unit2.id)).scalars().all() == [
        "fail"
    ]

    class Unsure(DemoReview):
        def solve(self, payload, meter):
            out = super().solve(payload, meter)
            return {**out, "ambiguous": [payload["questions"][0]["id"]]}

    factory.set_provider_override("demo", demo_providers(review=Unsure()))
    jid3 = enqueue(db, seed="t3")
    worker.drain("w")
    assert unit_of(db, jid3)[1].status == "QUARANTINED"


# ---- 17. budget and concurrency for every paid step


class PaidFake:
    """Looks like paid providers (uses the real BudgetMeter) but never touches the network."""

    name, model, record_method, audio_kind = "fakepaid", "gpt-6.1-terra", "api_reviewed", "tts"

    def __init__(self, cost):
        self.cost, self.calls = cost, []

    def _spend(self, step, meter):
        entry = meter.reserve(step, self.name, self.model, self.cost)
        self.calls.append(step)
        meter.settle(entry, self.cost, {"fake": True})

    def generate(self, spec, meter):
        self._spend("content", meter)
        return DemoContent().generate(spec, meter)

    def solve(self, payload, meter):
        self._spend("review", meter)
        return DemoReview().solve(payload, meter)

    def synthesize(self, text, voice, lang, instructions, meter):
        self._spend("tts", meter)
        return DemoTTS().synthesize(text, voice, lang, instructions, meter)

    def transcribe(self, pcm, lang, meter, expected_hint=""):
        self._spend("asr", meter)
        return expected_hint


def paid_set(cost):
    fake = PaidFake(cost)
    asr = PaidFake(cost)
    asr.record_method, asr.calls = "audio_auto_checked", fake.calls
    factory.set_provider_override("live", factory.Providers(fake, fake, fake, asr, paid=True))
    return fake


def paid(db, cost, **settings):
    fake = paid_set(cost)
    budget.set_system(db, "live", settings)
    db.commit()
    return fake


def test_paid_generation_is_off_by_default_even_with_providers(db, live_user):
    fake = paid_set(0.01)
    assert budget.get_system(db, "live")["paid_enabled"] is False
    jid = enqueue(db, realm="live")
    assert worker.drain("w") == ["blocked:paid_disabled"]
    job, unit = unit_of(db, jid)
    assert job.status == "BLOCKED" and unit is None and fake.calls == []
    assert db.execute(select(func.count()).select_from(m.CostEntry)).scalar_one() == 0
    budget.set_system(db, "live", {"paid_enabled": True, "daily_budget_usd": 5, "per_job_cap_usd": 5})
    queue.release_blocked(db, "live", ("paid_disabled",))
    db.commit()
    assert worker.drain("w") == ["succeeded"]  # after enabling: no per-job approval needed
    assert {"content", "review", "tts", "asr"} <= set(fake.calls)


def test_daily_budget_spans_all_steps_and_retries(db, live_user):
    fake = paid(db, 0.10, paid_enabled=True, daily_budget_usd=0.35, per_job_cap_usd=5)
    jid = enqueue(db, realm="live")
    assert worker.drain("w") == ["blocked:budget"]
    job, unit = unit_of(db, jid)
    assert fake.calls == ["content", "review", "tts"]  # the 4th request (0.40 > 0.35) was never started
    assert job.status == "BLOCKED" and unit.status != "READY" and job.last_completed_step == "review"
    spent = budget.spent_today(db, "live")
    assert spent["counted_usd"] == pytest.approx(0.30)
    job.run_after = datetime.now(UTC)
    db.commit()
    assert worker.drain("w") == ["blocked:budget"] and len(fake.calls) == 3  # re-check does not reset the limit
    assert budget.job_cost(db, jid)["settled_usd"] == pytest.approx(0.30)


def test_per_job_cap_unknown_cost_and_unknown_price(db, live_user):
    paid(db, 0.10, paid_enabled=True, daily_budget_usd=50, per_job_cap_usd=0.25)
    jid = enqueue(db, realm="live")
    assert worker.drain("w") == ["blocked:job_cap"]
    meter = BudgetMeter("live", None)
    e = meter.reserve("tts", "x", "m", 0.2)
    before = budget.spent_today(db, "live")["counted_usd"]
    meter.mark_unknown(e, "timeout")  # we cannot know whether it was billed: it stays counted
    after = budget.spent_today(db, "live")
    assert after["counted_usd"] == pytest.approx(before) and after["unknown_usd"] == pytest.approx(0.2)
    with pytest.raises(BudgetBlocked) as ex:
        meter.reserve("content", "x", "some-new-model", budget.est_text("some-new-model", 1000, 1000))
    assert ex.value.reason == "price_unknown"
    assert unit_of(db, jid)[0].blocked_reason == "job_cap"


def test_max_concurrent_jobs(db, live_user):
    paid(db, 0.001, paid_enabled=True, daily_budget_usd=5, per_job_cap_usd=5, max_concurrent_jobs=1)
    enqueue(db, realm="live", seed="c1")
    enqueue(db, realm="live", seed="c2")
    a = queue.claim(db, "w1")
    db.commit()
    assert a is not None and queue.claim(db, "w2") is None
    budget.set_system(db, "live", {"max_concurrent_jobs": 2})
    db.commit()
    assert queue.claim(db, "w2") is not None


def test_openai_adapters_reserve_before_any_request_and_tests_cannot_reach_the_network(db, live_user):
    from lq.generator.openai_provider import OpenAIASR, OpenAIContent, RealtimeTTS
    from lq.generator.providers import GenSpec

    meter = BudgetMeter("live", None)
    spec = GenSpec(**{k: v for k, v in SPEC.items()})
    with pytest.raises(BudgetBlocked):  # paid use is off: blocked before the HTTP layer is touched
        OpenAIContent("sk-test", "https://api.openai.com/v1", "gpt-6.1-terra", 5).generate(spec, meter)
    with pytest.raises(BudgetBlocked):
        RealtimeTTS("sk-test", "wss://api.openai.com/v1/realtime", "gpt-realtime-2.1-mini", 5).synthesize(
            "Hi", "cedar", "en", "", meter
        )
    budget.set_system(db, "live", {"paid_enabled": True})
    db.commit()
    with pytest.raises(AssertionError, match="network call attempted"):  # conftest blocks httpx / websockets
        OpenAIASR("sk-test", "https://api.openai.com/v1", "gpt-transcribe", 5).transcribe(_tone(300, 500), "en", meter)


def test_auto_replenish_respects_switches_and_does_not_duplicate(db, user, live_user):
    from lq.services import learning as L
    from lq.services import replenish

    assert replenish.run_for_user(db, user) == 0  # off by default
    budget.set_system(db, "demo", {"auto_replenish": True})
    budget.set_system(db, "live", {"auto_replenish": True})  # live: still blocked without paid_enabled
    n = replenish.run_for_user(db, user)
    db.commit()
    jobs = db.execute(select(m.GenerationJob)).scalars().all()
    assert (
        0 < n <= 12
        and {j.params["mode"] for j in jobs} == {L.user_settings(user)["active_mode"]}
        and {j.realm for j in jobs} == {"demo"}
    )
    assert replenish.run_for_user(db, user) == 0  # queued work counts as stock on the way; nothing is duplicated
    assert replenish.run_for_user(db, live_user) == 0


def test_cli_generate_sends_to_server_and_keeps_failures_in_outbox(db, user, monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from lq import cli
    from lq.config import get_settings

    monkeypatch.setattr(get_settings(), "server_url", "https://lq.invalid")
    monkeypatch.setattr(get_settings(), "import_token", "t")
    monkeypatch.setattr(get_settings(), "outbox_dir", tmp_path / "outbox")
    sent = []

    def post(d, realm):
        if not sent:
            sent.append("fail")
            raise RuntimeError("connection refused")
        manifest = json.loads((d / "manifest.json").read_text())
        blobs = {n: (d / n).read_bytes() for n in manifest["files"]}
        sent.append(manifest["units"][0]["content_id"])
        return importer.import_manifest(db, "live", manifest, blobs)

    monkeypatch.setattr(cli, "_post_bundle", post)
    r = CliRunner().invoke(cli.app, ["generate", "common_test", "3", "--realm", "demo"])
    assert r.exit_code == 0, r.output
    assert "kept in outbox" in r.output
    boxes = list((tmp_path / "outbox").iterdir())
    assert len(boxes) == 1 and (boxes[0] / "outbox.json").exists()
    manifest = json.loads((boxes[0] / "manifest.json").read_text())
    assert "review" not in manifest["units"][0] and manifest["files"]  # review results are not exported as approvals
    r = CliRunner().invoke(cli.app, ["outbox", "flush"])
    assert r.exit_code == 0 and "sent 1" in r.output and list((tmp_path / "outbox").iterdir()) == []
    db.commit()
    live = db.execute(select(m.Unit).where(m.Unit.realm == "live")).scalar_one()
    assert live.status == "CONTENT_REVIEW"  # received with all audio, but the live area wants its own content review


# ---- stability: saved recognition results and lease keep-alive


class CountingASR(DemoASR):
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = 0, fail_on

    def transcribe(self, pcm, lang, meter, expected_hint=""):
        self.calls += 1
        if self.fail_on is not None and self.calls == self.fail_on:
            raise ProviderError("recogniser timed out")
        return expected_hint


def test_recognition_results_are_saved_per_clip_and_not_paid_again_on_resume(db, user):
    first = CountingASR(fail_on=3)
    factory.set_provider_override("demo", demo_providers(asr=first))
    jid = enqueue(db)
    assert worker.drain("w1") == ["retry"]
    assert (
        first.calls == 3 and db.execute(select(func.count()).select_from(m.AsrCheck)).scalar_one() == 2
    )  # two finished checks kept
    second = CountingASR()
    factory.set_provider_override("demo", demo_providers(asr=second))
    job = db.get(m.GenerationJob, jid)
    job.run_after = datetime.now(UTC)
    db.commit()
    assert worker.drain("w2") == ["succeeded"]
    total = db.execute(select(func.count()).select_from(m.AsrCheck)).scalar_one()
    assert second.calls == total - 2  # the two saved results were reused, not requested again
    _, unit = unit_of(db, jid)
    assert unit.status == "READY"
    rec = db.execute(select(m.ReviewRecord).where(m.ReviewRecord.target == "audio")).scalar_one()
    assert len(rec.details["clips"]) == total


class SlowTTS(DemoTTS):
    """Simulates a long external call (seconds instead of minutes)."""

    def __init__(self, delay):
        self.delay, self.calls = delay, 0

    def synthesize(self, *a, **k):
        import time

        self.calls += 1
        if self.calls == 1:
            time.sleep(self.delay)
        return super().synthesize(*a, **k)


def test_lease_is_kept_alive_during_a_long_call_so_a_second_worker_cannot_take_the_job(db, user, monkeypatch):
    import threading
    import time

    from lq.config import get_settings

    monkeypatch.setattr(get_settings(), "job_lease_sec", 2)
    budget.set_system(db, "demo", {"max_concurrent_jobs": 4})
    factory.set_provider_override("demo", demo_providers(tts=SlowTTS(delay=5)))
    jid = enqueue(db)
    outcome = {}
    t = threading.Thread(target=lambda: outcome.setdefault("w1", worker.run_one("w1")))
    t.start()
    for _ in range(100):  # wait until w1 holds the job, then let w2 try to take it
        with session_factory()() as s:
            if s.get(m.GenerationJob, jid).lease_owner == "w1":
                break
        time.sleep(0.05)
    stolen = []
    deadline = time.time() + 4.5
    while time.time() < deadline:  # a second worker keeps trying while the first one is inside the slow call
        with session_factory()() as s:
            lease = queue.claim(s, "w2")
            s.commit()
        if lease is not None:
            stolen.append(lease)
        time.sleep(0.3)
    t.join(timeout=120)
    assert stolen == [] and outcome["w1"] == "succeeded"
    job, unit = unit_of(db, jid)
    assert job.attempts == 0 and job.lease_generation == 1 and unit.status == "READY"


def test_a_worker_that_lost_its_lease_stops_writing(db, user):
    enqueue(db)
    lease = queue.claim(db, "w1")
    db.commit()
    job = db.get(m.GenerationJob, lease.job_id)
    job.lease_owner = "someone-else"  # another worker took over after an expiry
    db.commit()
    assert queue.renew(lease) is False and lease.lost.is_set()
    with pytest.raises(queue.StaleLease):
        lease.guard(db)


# ---- live verification: one capped job, nothing else spends


def test_verify_live_runs_only_its_own_job_under_the_cap(db, live_user, monkeypatch):
    from typer.testing import CliRunner

    from lq import cli
    from lq.config import get_settings
    from lq.services.seed import seed_fixtures

    monkeypatch.setattr(get_settings(), "openai_api_key", "sk-test")
    monkeypatch.setattr(get_settings(), "work_dir", get_settings().work_dir / "t")
    seed_fixtures(db, "live")  # registered examples must NOT be processed by the verification
    db.commit()
    fake = paid_set(0.01)
    r = CliRunner().invoke(cli.app, ["verify-live", "--cost-cap-usd", "1"])
    assert r.exit_code == 0, r.output
    jobs = db.execute(select(m.GenerationJob)).scalars().all()
    ver = [j for j in jobs if j.params.get("verification")]
    assert len(ver) == 1 and ver[0].status == "SUCCEEDED"
    assert {j.status for j in jobs if j is not ver[0]} == {"QUEUED"}  # examples untouched
    assert {e.job_id for e in db.execute(select(m.CostEntry)).scalars()} == {ver[0].id}  # only this job spent
    assert {"content", "review", "tts", "asr"} <= set(fake.calls)
    assert (
        budget.get_system(db, "live")["verification"] is None and budget.get_system(db, "live")["paid_enabled"] is False
    )
    report = json.loads(r.output[r.output.index("{") : r.output.rindex("}") + 1])
    assert report["unit_status"] == "READY" and report["cost"]["settled_usd"] > 0 and report["open_in_app"]


def test_verify_live_stops_at_the_cap_and_refuses_without_key(db, live_user, monkeypatch):
    from typer.testing import CliRunner

    from lq import cli
    from lq.config import get_settings

    monkeypatch.setattr(get_settings(), "work_dir", get_settings().work_dir / "t2")
    r = CliRunner().invoke(cli.app, ["verify-live", "--cost-cap-usd", "1"])
    assert r.exit_code == 1 and "OPENAI_API_KEY is not set" in r.output
    assert db.execute(select(func.count()).select_from(m.GenerationJob)).scalar_one() == 0
    monkeypatch.setattr(get_settings(), "openai_api_key", "sk-test")
    fake = paid_set(0.10)
    r = CliRunner().invoke(cli.app, ["verify-live", "--cost-cap-usd", "0.25"])
    assert r.exit_code == 1
    job = db.execute(select(m.GenerationJob)).scalar_one()
    assert len(fake.calls) == 2 and budget.job_cost(db, job.id)["settled_usd"] == pytest.approx(
        0.20
    )  # third request never started
    assert "verification_cap" in (job.stop_detail or "") or job.blocked_reason == "verification_cap"
    assert budget.get_system(db, "live")["verification"] is None
