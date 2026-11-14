"""Job pipeline: content -> blind review -> audio -> audio QA -> READY.

Every step commits on its own and re-proves the lease first, so a restarted worker resumes from the
unit's stored status and a stale worker cannot write. Objects go to storage before the DB says READY."""

from __future__ import annotations

import dataclasses
import logging

from pydantic import ValidationError
from sqlalchemy import delete, select

from lq.audio import pitch, qa
from lq.audio import render as R
from lq.audio.pcm import AudioError, decode_to_pcm, duration_ms, encode_mp3, wav_bytes
from lq.db import models as m
from lq.db.base import new_id, session_factory
from lq.domain.content import UnitContent, canonical_json, reviewer_view, sha256_hex
from lq.domain.profiles import get_profile, part_spec
from lq.generator import authoring
from lq.generator.budget import BudgetBlocked
from lq.generator.factory import assign_voices, get_providers, speaker_gender
from lq.generator.providers import GenSpec, ProviderError
from lq.jobs.queue import Lease, StaleLease
from lq.services import content as C
from lq.storage import get_storage

log = logging.getLogger("lq.pipeline")

CACHE_IMPL_VERSION = 1
AUDIO_SETTINGS = {"rate": 24000, "channels": 1, "format": "s16le"}


class Blocked(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason, self.detail = reason, detail


class Terminal(Exception):
    """The job is over without success (e.g. the unit was quarantined). Not retried."""


def cache_key(text: str, provider: str, model: str, voice: str, speaker: str, lang: str, instructions: str) -> str:
    return sha256_hex(
        canonical_json(
            {
                "text": text,
                "provider": provider,
                "model": model,
                "voice": voice,
                "speaker": speaker,
                "lang": lang,
                "instructions": instructions,
                "audio": AUDIO_SETTINGS,
                "impl": CACHE_IMPL_VERSION,
            }
        )
    )


def cached_clip(session, key: str) -> bytes | None:
    """Only finished, verified audio is ever in the cache; the stored hash is re-checked on read."""
    row = session.get(m.AudioCache, key)
    if row is None:
        return None
    st = get_storage()
    if not st.exists(row.storage_key):
        session.delete(row)
        session.flush()
        return None
    data = st.get(row.storage_key)
    if sha256_hex(data) != row.sha256:
        session.delete(row)
        session.flush()
        st.delete(row.storage_key)
        return None
    return decode_to_pcm(data)


def store_clip(session, key: str, pcm: bytes, provider: str, model: str, voice: str) -> str:
    """Atomic confirm: the object is written completely (temp + rename / single PUT), then the row is added."""
    data = wav_bytes(pcm)
    storage_key = f"cache/tts/{key[:2]}/{key}.wav"
    get_storage().put(storage_key, data, "audio/wav")
    if session.get(m.AudioCache, key) is None:
        session.add(
            m.AudioCache(
                cache_key=key,
                storage_key=storage_key,
                sha256=sha256_hex(data),
                duration_ms=duration_ms(pcm),
                provider=provider,
                model=model,
                voice=voice,
            )
        )
    return storage_key


def _commit_step(lease: Lease, fn, step: str | None = None):
    with session_factory()() as s:
        job = lease.guard(s)
        out = fn(s, job)
        if step:
            job.last_completed_step = step
        s.commit()
        return out


def execute(lease: Lease) -> str:
    """Run the job until it finishes, blocks or raises. Returns a short outcome string."""
    with session_factory()() as s:
        job = s.get(m.GenerationJob, lease.job_id)
        realm, kind, params, unit_id = job.realm, job.kind, dict(job.params), job.unit_id
    prov = get_providers(realm)
    meter = prov.meter(realm, lease.job_id)
    if kind == "generate" and unit_id is None:
        unit_id = step_content(lease, prov, meter, realm, params)
    while True:
        with session_factory()() as s:
            unit = s.get(m.Unit, unit_id)
            status, reason = unit.status, unit.status_reason
        if status == "READY":
            return "ready"
        if status in ("QUARANTINED", "FAILED"):
            raise Terminal(f"unit {status.lower()}: {reason}")
        if status in ("DRAFT", "CONTENT_REVIEW"):
            step_review(lease, prov, meter, unit_id)
        elif status in ("AUDIO_PENDING", "AUDIO_GENERATING"):
            step_audio(lease, prov, meter, unit_id)
        elif status == "AUDIO_QA":
            step_audio_qa(lease, prov, meter, unit_id)
        else:
            raise Terminal(f"unexpected unit status {status}")


# ---------------------------------------------------------------- content


def step_content(lease: Lease, prov, meter, realm: str, params: dict) -> str:
    if prov.content is None:
        raise Blocked("no_provider", "no content provider configured (set OPENAI_API_KEY, or import material)")
    spec = GenSpec(
        **{k: (tuple(v) if k == "avoid_topics" else v) for k, v in params.items() if k in GenSpec.__dataclass_fields__}
    )
    last_err = ""
    content = None
    lint_fixes: list[str] = []
    for attempt in range(3):  # schema violations / format-check failures: bounded retries, each one budgeted
        ask = spec if not lint_fixes else dataclasses.replace(spec, extra={**spec.extra, "fix_notes": lint_fixes})
        raw = prov.content.generate(ask, meter)
        try:
            cand = UnitContent.model_validate(raw)
        except ValidationError as e:
            last_err = str(e)[:800]
            continue
        # Format rules need no model, so check them before paying for review and audio. One rewrite with the
        # failures fed back; if the rewrite still fails, keep it and let step_review quarantine it with reasons.
        lint = authoring.lint_unit(cand) if cand.source != "legacy-radio" else []
        content = cand
        if not lint or lint_fixes or attempt == 2:
            break
        lint_fixes = lint[:10]
        log.info("content draft failed format checks, asking for one rewrite: %s", "; ".join(lint_fixes))
    if content is None:
        raise ProviderError(f"generated content violated the schema 3 times: {last_err}", retryable=False)

    def save(s, job):
        nonlocal content
        try:
            unit, _ = C.register_unit(s, realm, content, pool=spec.pool)
        except C.ContentConflict:
            content = content.model_copy(update={"content_id": f"{content.content_id}-{job.id[:6]}"})
            unit, _ = C.register_unit(s, realm, content, pool=spec.pool)
        job.unit_id = unit.id
        return unit.id

    return _commit_step(lease, save, "content")


# ---------------------------------------------------------------- blind review


def step_review(lease: Lease, prov, meter, unit_id: str) -> None:
    with session_factory()() as s:
        unit = s.get(m.Unit, unit_id)
        content = C.load_content(unit)
        has_pass = C.passing_review(s, unit, "content") is not None or C.carry_over_review(s, unit) is not None
        s.commit()
    lint = (
        []
        if content.legacy_radio_only
        else authoring.lint_unit(content, require_distractors=content.source != "legacy-radio")
    )
    if lint:
        _commit_step(
            lease, lambda s, job: C.quarantine(s, s.get(m.Unit, unit_id), "format check failed: " + "; ".join(lint))
        )
        return
    if not has_pass:
        if prov.review is None or content.legacy_radio_only:
            raise Blocked(
                "awaiting_content_review",
                "no reviewer configured; approve the content manually (lq approve) or enable API review",
            )
        payload = reviewer_view(content)  # learner-visible information only
        solved = prov.review.solve(payload, meter)
        if solved.get("unavailable"):
            raise Blocked(
                "awaiting_content_review", "the reviewer cannot judge this material; approve the content manually"
            )
        verdict = authoring.compare_review(content, solved)

        def record(s, job):
            unit = s.get(m.Unit, unit_id)
            C.add_review_record(s, unit, target="content", method=prov.review.record_method, reviewer=f"{prov.review.name}:{prov.review.model}",
                                result="pass" if verdict["ok"] else "fail", details={"problems": verdict["problems"], "notes": solved.get("notes", "")[:2000]})  # fmt: skip
            if not verdict["ok"]:
                C.quarantine(
                    s, unit, "blind review disagreed with the author's key: " + canonical_json(verdict["problems"])
                )

        _commit_step(lease, record)
        if not verdict["ok"]:
            return
    _commit_step(lease, lambda s, job: setattr(s.get(m.Unit, unit_id), "status", "AUDIO_PENDING"), "review")


# ---------------------------------------------------------------- audio


def _put_asset(st, unit_id: str, kind: str, role: str, pcm: bytes, meta: dict) -> dict:
    data = encode_mp3(pcm)
    check = qa.check_encoded(data, duration_ms(pcm), expect_sound=True)
    if not check["ok"]:
        raise AudioError(f"{role}: {check['problems']}")
    aid = new_id()
    key = f"units/{unit_id}/{aid}.mp3"  # opaque name: nothing about answers in the path
    st.put(key, data, "audio/mpeg")
    return {
        "id": aid,
        "kind": kind,
        "role": role,
        "storage_key": key,
        "sha256": sha256_hex(data),
        "bytes": len(data),
        "duration_ms": check["duration_ms"],
        "meta": meta,
    }


def step_audio(lease: Lease, prov, meter, unit_id: str) -> None:
    st = get_storage()
    with session_factory()() as s:
        unit = s.get(m.Unit, unit_id)
        content = C.load_content(unit)
        job = s.get(m.GenerationJob, lease.job_id)
        imported: dict = dict((job.state or {}).get("import_audio") or {})
    _commit_step(lease, lambda s, job: setattr(s.get(m.Unit, unit_id), "status", "AUDIO_GENERATING"))

    if "radio_complete" in imported:  # legacy answer-included MP3: radio only, never an answering candidate
        pcm = decode_to_pcm(st.get(imported["radio_complete"]))
        asset = _put_asset(st, unit_id, "radio", "radio", pcm, {"timeline": [], "legacy_complete": True})
        _save_assets(lease, unit_id, [asset], {}, "imported")
        return

    specs = R.plan_clips(content)
    voices = assign_voices(content)
    clips: dict[str, bytes] = {}
    clip_keys: dict[str, str] = {}
    missing = [cid for cid in specs if cid not in imported]
    if missing and prov.tts is None:
        raise Blocked(
            "missing_audio",
            f"{len(missing)} required clips have no audio and no speech provider is configured: {', '.join(missing[:8])}",
        )
    used_tts = False
    for cid, spec in specs.items():
        if cid in imported:
            clips[cid] = decode_to_pcm(st.get(imported[cid]))
            clip_keys[cid] = imported[cid]
            continue
        voice = voices.get(spec.speaker_id, voices["narrator"])
        instructions = "Short Japanese explanation, calm teacher voice." if spec.lang == "ja" else ""
        key = cache_key(spec.text, prov.tts.name, prov.tts.model, voice, spec.speaker_id, spec.lang, instructions)
        with session_factory()() as s:
            pcm = cached_clip(s, key)
            s.commit()
        if pcm is None:
            _commit_step(lease, lambda s, job: None)  # heartbeat + lease proof before spending
            pcm = prov.tts.synthesize(spec.text, voice, spec.lang, instructions, meter)
            problems = qa.check_clip(pcm, spec.text)
            if problems:
                raise AudioError(f"clip {cid}: {problems}")  # not cached: unfinished/bad audio is never reused
            with session_factory()() as s:
                store_clip(s, key, pcm, prov.tts.name, prov.tts.model, voice)
                s.commit()
        used_tts = True
        clips[cid] = pcm
        clip_keys[cid] = f"cache/tts/{key[:2]}/{key}.wav"

    profile = get_profile(content.profile_version)
    part = part_spec(profile, content.part) or {}
    assets = []
    for seg in content.segments:
        t = R.render_stimulus(content, seg.id, clips)
        assets.append(_put_asset(st, unit_id, "stimulus", f"stimulus:{seg.id}", t.pcm(), {"timeline": t.timeline}))
    t = R.render_exam(content, clips, part, profile.get("timing", {}))
    assets.append(_put_asset(st, unit_id, "exam", "exam", t.pcm(), {"timeline": t.timeline}))
    t = R.render_feedback(content, clips)
    assets.append(
        _put_asset(
            st,
            unit_id,
            "feedback",
            "feedback",
            t.pcm(),
            {"timeline": t.timeline, "transcript": R.transcript_of(t, content, specs)},
        )
    )
    if R.radio_supported(content)[0]:
        t = R.render_radio(content, clips)
        assets.append(
            _put_asset(
                st,
                unit_id,
                "radio",
                "radio",
                t.pcm(),
                {"timeline": t.timeline, "transcript": R.transcript_of(t, content, specs)},
            )
        )
    for seg in content.segments:
        for u in seg.utterances:
            assets.append(_put_asset(st, unit_id, "utterance", f"utt:{u.id}", clips[f"utt:{u.id}"], {}))
    kind = prov.tts.audio_kind if (used_tts and prov.tts is not None) else "imported"
    _save_assets(lease, unit_id, assets, clip_keys, kind)


def _save_assets(lease: Lease, unit_id: str, assets: list[dict], clip_keys: dict, audio_kind: str) -> None:
    def save(s, job):
        unit = s.get(m.Unit, unit_id)
        old = s.execute(select(m.Asset).where(m.Asset.unit_id == unit_id)).scalars().all()
        for a in old:  # leftovers of an interrupted earlier run; their objects are collected by gc
            s.delete(a)
        s.flush()
        for a in assets:
            s.add(
                m.Asset(
                    id=a["id"],
                    unit_id=unit_id,
                    kind=a["kind"],
                    role=a["role"],
                    storage_key=a["storage_key"],
                    sha256=a["sha256"],
                    bytes=a["bytes"],
                    duration_ms=a["duration_ms"],
                    quality_state="decoded_ok",
                    meta=a["meta"],
                )
            )
        unit.audio_kind = audio_kind
        unit.status = "AUDIO_QA"
        job.state = {**(job.state or {}), "clip_keys": clip_keys}

    _commit_step(lease, save, "audio")


ASR_IMPL_VERSION = 1


def asr_cache_key(audio: bytes, provider: str, model: str, lang: str, expected: str) -> str:
    # expected text is part of the key only so a mock recogniser that echoes it stays correct; a real
    # recogniser never receives it, and identical audio always comes from identical text anyway
    return sha256_hex(
        canonical_json(
            {
                "audio": sha256_hex(audio),
                "provider": provider,
                "model": model,
                "lang": lang,
                "expected": expected,
                "impl": ASR_IMPL_VERSION,
            }
        )
    )


def transcribe_cached(lease: Lease, asr, meter, audio: bytes, lang: str, expected: str) -> str:
    """Reuse a stored transcription of the same clip; otherwise transcribe (paid) and store it at once."""
    key = asr_cache_key(audio, asr.name, asr.model, lang, expected)
    with session_factory()() as s:
        row = s.get(m.AsrCheck, key)
        if row is not None:
            return row.transcript
    _commit_step(lease, lambda s, job: None)  # prove the lease before spending
    heard = asr.transcribe(decode_to_pcm(audio), lang, meter, expected_hint=expected)
    with session_factory()() as s:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        s.execute(
            pg_insert(m.AsrCheck)
            .values(
                cache_key=key,
                audio_sha256=sha256_hex(audio),
                provider=asr.name,
                model=asr.model,
                lang=lang,
                transcript=heard,
                job_id=lease.job_id,
            )
            .on_conflict_do_nothing()
        )
        s.commit()
    return heard


def step_audio_qa(lease: Lease, prov, meter, unit_id: str) -> None:
    st = get_storage()
    with session_factory()() as s:
        unit = s.get(m.Unit, unit_id)
        content = C.load_content(unit)
        assets = s.execute(select(m.Asset).where(m.Asset.unit_id == unit_id)).scalars().all()
        job = s.get(m.GenerationJob, lease.job_id)
        clip_keys = dict((job.state or {}).get("clip_keys") or {})
        approved = C.passing_review(s, unit, "audio")
        approved_method = approved.method if approved else None
        asset_info = [(a.role, a.storage_key, a.duration_ms) for a in assets]
        audio_kind = unit.audio_kind
    problems = []
    for role, key, ms in asset_info:
        if not st.exists(key):
            problems.append(f"{role}: object missing")
            continue
        chk = qa.check_encoded(st.get(key), ms)
        if not chk["ok"]:
            problems.append(f"{role}: {chk['problems']}")
    if problems:
        _commit_step(
            lease, lambda s, job: C.quarantine(s, s.get(m.Unit, unit_id), "audio check failed: " + "; ".join(problems))
        )
        return

    state = {
        "audio_human_listened": "human_listened",
        "audio_auto_checked": "auto_checked",
        "audio_demo_mock_checked": "mock_checked",
    }
    if approved_method is None:
        if prov.asr is None or not clip_keys:
            raise Blocked(
                "awaiting_audio_approval",
                "no independent speech recognition configured (or legacy audio without a script): listen and approve the audio manually",
            )
        specs = R.plan_clips(content)
        evidence_utts = {e.utterance_id for q in content.questions for e in q.evidence if e.utterance_id}
        results, bad = [], []
        for cid, spec in specs.items():
            if spec.lang != "en" or cid not in clip_keys:
                continue  # Japanese explanations: decode/level checks only
            heard = transcribe_cached(lease, prov.asr, meter, st.get(clip_keys[cid]), spec.lang, spec.text)
            cmp = qa.compare_transcript(
                spec.text, heard, is_evidence=cid.startswith("utt:") and cid[4:] in evidence_utts
            )
            results.append({"clip": cid, "wer": cmp["wer"], "ok": cmp["ok"]})
            if not cmp["ok"]:
                bad.append({"clip": cid, "problems": cmp["problems"], "heard": heard[:300]})

        # Speech recognition cannot hear WHO speaks: check that each speaker's voice pitch fits the speaker's
        # gender (label or voice_hint). Demo tones are not speech, so they are skipped. A mismatch comes from the
        # voice choice, so synthesising again with the same voice would not help: it quarantines directly.
        voice_bad = []
        if audio_kind == "tts":  # synthesised speech only; imported recordings are approved by a person
            genders = {sp.id: speaker_gender(sp) for sp in content.speakers if sp.role != "narrator"}
            for cid, spec in specs.items():
                if (
                    not cid.startswith("utt:")
                    or cid not in clip_keys
                    or genders.get(spec.speaker_id, "neutral") == "neutral"
                ):
                    continue
                f0 = pitch.median_f0(decode_to_pcm(st.get(clip_keys[cid])))
                problem = pitch.gender_mismatch(genders[spec.speaker_id], f0)
                results_by = next((r for r in results if r["clip"] == cid), None)
                if results_by is not None:
                    results_by["pitch_hz"] = round(f0) if f0 else None
                if problem:
                    voice_bad.append({"clip": cid, "speaker": spec.speaker_id, "problems": [problem]})

        # A synthesised clip that does not match gets ONE fresh synthesis before the unit is quarantined:
        # speech models are not deterministic. Only that clip is re-made (one more speech request); all other
        # clips and their recognition results come from the cache, so nothing else is paid again.
        retry = [b["clip"] for b in bad if clip_keys.get(b["clip"], "").startswith("cache/tts/")]
        if bad and retry and len(retry) == len(bad) and not voice_bad:

            def resynth(s, job):
                st_job = dict(job.state or {})
                done = dict(st_job.get("resynth") or {})
                if any(done.get(c, 0) >= 1 for c in retry):
                    return False
                for c in retry:
                    # forget the failed clip and its recognition, so both are really made again
                    s.execute(
                        delete(m.AsrCheck).where(m.AsrCheck.audio_sha256 == sha256_hex(get_storage().get(clip_keys[c])))
                    )
                    key = clip_keys[c].rsplit("/", 1)[1].removesuffix(".wav")
                    row = s.get(m.AudioCache, key)
                    if row is not None:
                        s.delete(row)
                    done[c] = done.get(c, 0) + 1
                st_job["resynth"] = done
                st_job["resynth_log"] = [*st_job.get("resynth_log", []), {"clips": retry, "mismatches": bad}]
                job.state = st_job
                s.get(m.Unit, unit_id).status = "AUDIO_PENDING"
                return True

            if _commit_step(lease, resynth):
                return

        def record(s, job):
            unit = s.get(m.Unit, unit_id)
            C.add_review_record(
                s,
                unit,
                target="audio",
                method=prov.asr.record_method,
                reviewer=f"{prov.asr.name}:{prov.asr.model}",
                result="fail" if bad or voice_bad else "pass",
                details={"clips": results, "mismatches": bad, "voice_mismatches": voice_bad},
            )
            if voice_bad:
                C.quarantine(s, unit, "voice does not fit the speaker: " + canonical_json(voice_bad)[:1500])
            elif bad:
                C.quarantine(s, unit, "speech recognition did not match the script: " + canonical_json(bad)[:1500])

        _commit_step(lease, record)
        if bad or voice_bad:
            return
        approved_method = prov.asr.record_method

    def ready(s, job):
        C.finalize_ready(s, s.get(m.Unit, unit_id), state[approved_method])

    _commit_step(lease, ready, "ready")


__all__ = ["execute", "Blocked", "Terminal", "BudgetBlocked", "StaleLease", "cache_key", "cached_clip", "store_clip"]
