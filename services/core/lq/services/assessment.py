"""Assessments under exam conditions: untouched reserved material, fixed at start, no aids, no feedback
until the whole exam is submitted. Common Test / Eiken sets follow the official profile exactly (full
mock); C1 / C2 sets are an app-defined "in-app check"."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import or_, select

from lq.db import models as m
from lq.domain.content import canonical_json, is_correct, learner_view, normalize_answer, sha256_hex
from lq.domain.profiles import current_profile, get_profile, score_exam, slot_layout, total_slots
from lq.generator.authoring import contract_errors, lint_unit
from lq.jobs import queue
from lq.services import learning as L
from lq.services.content import load_content


class Shortage(Exception):
    def __init__(self, detail: dict):
        super().__init__("not enough reserved material")
        self.detail = detail


def _in_use_unit_ids(session, realm: str) -> set[str]:
    used: set[str] = set()
    for ms in session.execute(
        select(m.MockSet).where(m.MockSet.realm == realm, m.MockSet.status.in_(("READY", "USED")))
    ).scalars():
        used.update(i["unit_id"] for i in ms.items)
    return used


def fits_profile(session, unit: m.Unit, profile: dict) -> bool:
    """R06: material of the current format version always fits. Older versions of the same exam are re-checked for
    free against the current part contract (nothing is regenerated or overwritten); only those that pass are used."""
    if unit.profile_version == profile["id"]:
        return True
    part = next((p for p in profile["parts"] if p["part"] == unit.part), None)
    if part is None:
        return False
    return not contract_errors(load_content(unit), part)


def _arranged(session, unit: m.Unit) -> bool:
    return load_content(unit).presentation_profile.kind != "official"


def reserved_inventory(session, realm: str, mode: str) -> dict:
    """Per part: reserved READY units not yet placed in a set, versus what one set needs."""
    profile = current_profile(mode)
    used = _in_use_unit_ids(session, realm)
    U = m.Unit
    rows = (
        session.execute(
            select(U).where(
                U.realm == realm,
                U.mode == mode,
                U.pool == "assessment",
                U.status == "READY",
                U.invalidated_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    pending = (
        session.execute(
            select(m.GenerationJob).where(
                m.GenerationJob.realm == realm,
                m.GenerationJob.status.in_(("QUEUED", "RUNNING", "RETRY_WAIT", "BLOCKED")),
            )
        )
        .scalars()
        .all()
    )
    parts = []
    for p in profile["parts"]:
        have = [
            u
            for u in rows
            if u.part == p["part"]
            and u.id not in used
            and u.question_count == p["questions_per_unit"]
            and "assessment" in u.session_types
            and fits_profile(session, u, profile)
        ]
        inflight = sum(
            1
            for j in pending
            if j.params.get("mode") == mode
            and j.params.get("part") == p["part"]
            and j.params.get("pool") == "assessment"
        )
        parts.append(
            {
                "part": p["part"],
                "label_ja": p["label_ja"],
                "need": p["units"],
                "have": len(have),
                "in_progress": inflight,
            }
        )
    return {
        "mode": mode,
        "profile_id": profile["id"],
        "exam_label": profile["exam"]["label_ja"],
        "parts": parts,
        "complete": all(x["have"] >= x["need"] for x in parts),
    }


def enqueue_missing(session, realm: str, mode: str, seed: str) -> int:
    """Queue generation jobs (pool=assessment) for whatever one full set still lacks."""
    inv = reserved_inventory(session, realm, mode)
    profile = current_profile(mode)
    skills = [
        "explicit_detail",
        "numbers_negation",
        "inference",
        "speaker_attitude",
        "main_idea",
        "discourse_structure",
        "condition_matching",
    ]
    made = 0
    for p in inv["parts"]:
        spec = next(x for x in profile["parts"] if x["part"] == p["part"])
        for i in range(max(0, p["need"] - p["have"] - p["in_progress"])):
            skill = (
                "integration"
                if spec["template_type"] in ("lecture_integrated", "condition_choice")
                else skills[(i + made) % len(skills)]
            )
            params = {
                "mode": mode,
                "part": p["part"],
                "template_type": spec["template_type"],
                "primary_skill": skill,
                "band": 3,
                "seed": f"mock-{seed}-{p['part']}-{i}",
                "source": "generated",
                "pool": "assessment",
            }
            _, created = queue.enqueue(
                session, realm, "generate", params, dedupe_key=f"mock:{realm}:{mode}:{seed}:{p['part']}:{i}"
            )
            made += 1 if created else 0
    return made


def assemble(session, realm: str, mode: str) -> m.MockSet:
    """Build one set once every part is fully stocked. Validates format, answer slots and speakers
    against the profile; a shortened set is never produced."""
    profile = current_profile(mode)
    used = _in_use_unit_ids(session, realm)
    U = m.Unit
    rows = (
        session.execute(
            select(U)
            .where(
                U.realm == realm,
                U.mode == mode,
                U.pool == "assessment",
                U.status == "READY",
                U.invalidated_at.is_(None),
            )
            .order_by(U.created_at, U.id)
        )
        .scalars()
        .all()
    )
    exposed = set(session.execute(select(m.ExposureEvent.unit_id).distinct()).scalars())
    by_part: dict[str, list[m.Unit]] = {}
    for u in rows:
        if u.id in used or u.id in exposed or "assessment" not in u.session_types:
            continue
        if not fits_profile(session, u, profile):
            continue
        by_part.setdefault(u.part, []).append(u)
    layout = slot_layout(profile)
    items, problems, families, duration_ms = [], [], set(), 0
    shortage = {}
    cursor: dict[str, int] = {}
    for entry in layout:
        pool = by_part.get(entry["part"], [])
        i = cursor.get(entry["part"], 0)
        unit = None
        while i < len(pool):  # one unit per family inside a set
            if pool[i].family_id not in families:
                unit = pool[i]
                i += 1
                break
            i += 1
        cursor[entry["part"]] = i
        if unit is None:
            shortage[entry["part"]] = shortage.get(entry["part"], 0) + 1
            continue
        families.add(unit.family_id)
        content = load_content(unit)
        errs = lint_unit(content)
        if errs:
            problems.append(f"{unit.id}: {errs}")
        qs = content.sorted_questions()
        if len(qs) != len(entry["slots"]):
            problems.append(f"{unit.id}: question count does not match the answer slots")
            continue
        exam = session.execute(
            select(m.Asset).where(m.Asset.unit_id == unit.id, m.Asset.kind == "exam")
        ).scalar_one_or_none()
        if exam is None:
            problems.append(f"{unit.id}: exam audio missing")
            continue
        duration_ms += exam.duration_ms
        items.append(
            {
                "unit_id": unit.id,
                "part": entry["part"],
                "section": entry["section"],
                "slots": {q.id: s for q, s in zip(qs, entry["slots"], strict=True)},
                "duration_ms": exam.duration_ms,
                "presentation": content.presentation_profile.kind,
            }
        )
    if shortage:
        raise Shortage({"missing_units_by_part": shortage})
    if problems:
        raise L.Invalid("set validation failed: " + "; ".join(problems))
    n = sum(len(i["slots"]) for i in items)
    if n != total_slots(profile):
        raise L.Invalid(f"set has {n} answer slots, profile needs {total_slots(profile)}")
    ms = m.MockSet(realm=realm, mode=mode, profile_id=profile["id"], profile_snapshot=profile, kind=profile["exam"]["kind"], items=items, duration_sec=round(duration_ms / 1000),
                   validation={"slots": n, "units": len(items), "checked": ["format", "contract", "answer_slots", "speakers", "plays", "scoring_groups", "exam_audio"],
                               "arranged_parts": sorted({i["part"] for i in items if i["presentation"] != "official"})})  # fmt: skip
    session.add(ms)
    session.flush()
    return ms


def set_profile(ms: m.MockSet) -> dict:
    """R05: the profile a set was built with. Older sets without a snapshot use the stored profile id exactly;
    an id that no longer exists is an error, never a silent switch to the current version."""
    if ms.profile_snapshot:
        return ms.profile_snapshot
    try:
        return get_profile(ms.profile_id)
    except KeyError as e:
        raise L.Conflict(f"this set was built with profile {ms.profile_id}, which is no longer available") from e


def session_profile(session, sess: m.LearningSession) -> dict:
    ms = session.get(m.MockSet, sess.mock_set_id) if sess.mock_set_id else None
    if ms is None:
        raise L.Conflict("the set of this assessment is missing")
    return set_profile(ms)


def mock_set_dto(ms: m.MockSet) -> dict:
    profile = set_profile(ms)
    return {"id": ms.id, "mode": ms.mode, "profile_id": ms.profile_id, "kind": ms.kind, "label": profile["exam"]["label_ja"], "status": ms.status, "units": len(ms.items),
            "slots": sum(len(i["slots"]) for i in ms.items), "duration_sec": ms.duration_sec, "venue_note_ja": profile["exam"]["venue_note_ja"],
            "answer_time_min": profile["exam"].get("answer_time_min"), "created_at": ms.created_at.isoformat()}  # fmt: skip


def start(session, user: m.User, mock_set_id: str, now: datetime | None = None) -> m.LearningSession:
    now = now or datetime.now(UTC)
    ms = session.execute(select(m.MockSet).where(m.MockSet.id == mock_set_id).with_for_update()).scalar_one_or_none()
    if ms is None or ms.realm != user.realm:
        raise L.NotFound("set not found")
    if ms.status != "READY":
        raise L.Conflict("this set was already used or is no longer valid")
    units = [session.get(m.Unit, i["unit_id"]) for i in ms.items]
    # re-check contact history at the moment of starting: unit, same audio, same family
    E = m.ExposureEvent
    hit = session.execute(
        select(E.unit_id)
        .where(
            E.user_id == user.id,
            or_(
                E.unit_id.in_([u.id for u in units]),
                E.stimulus_fingerprint.in_([u.stimulus_fingerprint for u in units]),
                E.family_id.in_([u.family_id for u in units]),
            ),
        )
        .limit(1)
    ).first()
    bad = [u.id for u in units if u.status != "READY" or u.invalidated_at is not None]
    if hit or bad:
        ms.status = "STALE"
        session.flush()
        raise StaleSet(
            "this set contains material you have already been in contact with (or that was withdrawn); assemble a new set"
        )
    ms.status = "USED"
    sess = m.LearningSession(
        user_id=user.id,
        session_type="assessment",
        mode=ms.mode,
        mock_set_id=ms.id,
        meta={"day": L.user_day(user, now).isoformat(), "items": ms.items},
    )
    session.add(sess)
    session.flush()
    for u in units:
        L.add_exposure(session, user.id, u, "assessment", session_id=sess.id, at=now)
    return sess


class StaleSet(L.Conflict):
    pass


def get_session(session, user: m.User, session_id: str, lock: bool = False) -> m.LearningSession:
    q = select(m.LearningSession).where(m.LearningSession.id == session_id)
    sess = session.execute(q.with_for_update() if lock else q).scalar_one_or_none()
    if sess is None or sess.user_id != user.id or sess.session_type != "assessment":
        raise L.NotFound("assessment not found")
    return sess


def paper_dto(session, sess: m.LearningSession) -> dict:
    """The exam paper: learner views + exam audio only. No scripts, answers, feedback or aids."""
    profile = session_profile(session, sess)
    items = []
    for it in sess.meta["items"]:
        unit = session.get(m.Unit, it["unit_id"])
        view = learner_view(load_content(unit))
        view.pop("title", None)
        exam = L.unit_assets(session, unit.id).get("exam")
        for q in view["questions"]:
            q["global_slot"] = it["slots"][q["id"]]
        part = next(p for p in profile["parts"] if p["part"] == it["part"])
        items.append(
            {
                "unit_id": unit.id,
                "part": it["part"],
                "part_label": part["label_ja"],
                "section": it["section"],
                "audio": {"url": L.audio_url(exam), "duration_ms": exam.duration_ms},
                "unit": view,
                "audio_kind": unit.audio_kind,
            }
        )
    return {"session_id": sess.id, "mode": sess.mode, "status": sess.status, "label": profile["exam"]["label_ja"], "kind": profile["exam"]["kind"],
            "duration_sec": round(sum(i["duration_ms"] for i in sess.meta["items"]) / 1000), "venue_note_ja": profile["exam"]["venue_note_ja"],
            "slot_count": total_slots(profile), "items": items}  # fmt: skip


def submit(session, user: m.User, session_id: str, answers: dict, now: datetime | None = None) -> dict:
    """answers: {global slot (string): answer object or null}. Unanswered slots score 0. Idempotent."""
    now = now or datetime.now(UTC)
    sess = get_session(session, user, session_id, lock=True)
    profile = session_profile(session, sess)
    slot_correct: dict[int, bool] = {}
    per_unit = []
    normalized_all = {}
    for it in sess.meta["items"]:
        unit = session.get(m.Unit, it["unit_id"])
        content = load_content(unit)
        qmap = content.question_map()
        ua = {}
        for qid, slot in it["slots"].items():
            raw = answers.get(str(slot))
            try:
                ans = normalize_answer(qmap[qid], raw)
            except ValueError as e:
                raise L.Invalid(str(e)) from e
            ua[qid] = ans
            normalized_all[str(slot)] = ans
            slot_correct[slot] = is_correct(qmap[qid], ans)
        per_unit.append((unit, content, it, ua))
    h = sha256_hex(canonical_json(normalized_all))
    if sess.status == "submitted":
        if sess.submit_hash == h:
            return sess.result
        raise L.Conflict("this assessment was already submitted with different answers")
    score = score_exam(profile, slot_correct)
    today = L.user_day(user, now)
    for unit, content, it, ua in per_unit:
        per_q = {
            qid: {
                "answer": a,
                "correct": slot_correct[it["slots"][qid]],
                "skipped": a is None,
                "system_guess": None if slot_correct[it["slots"][qid]] else content.question_map()[qid].skill_tags[0],
            }
            for qid, a in ua.items()
        }
        complete = all(a is not None for a in ua.values())
        n_ok = sum(1 for v in per_q.values() if v["correct"])
        att = m.Attempt(
            id=sha256_hex(f"{sess.id}:{unit.id}")[:32], user_id=user.id, unit_id=unit.id, stimulus_fingerprint=unit.stimulus_fingerprint, mode=unit.mode, session_id=sess.id,
            session_type="assessment", presentation="official", status="submitted", first_claim=False, eligible_at_start=True, band_at_start=unit.difficulty_band,
            plays={}, aids={}, answers=ua, answer_hash=sha256_hex(canonical_json(ua)), complete=complete, unit_score=n_ok / len(per_q) if complete else None,
            strict_first=False, category="assessment", not_first_reasons=["session_type:assessment"], learning_day=today, started_at=sess.started_at, submitted_at=now,
            result={"questions": per_q, "correct_count": n_ok, "total": len(per_q), "complete": complete, "assisted": False, "skills_wrong": [], "skills_right": []},
        )  # fmt: skip
        session.add(att)
        L.add_exposure(
            session,
            user.id,
            unit,
            "answer_access",
            attempt_id=att.id,
            session_id=sess.id,
            meta={"via": "assessment_result"},
            at=now,
        )
        # the material is now known: it joins the practice pool for review / radio, never again as "first"
        unit.pool = "practice"
        roles = set(L.unit_assets(session, unit.id))
        from lq.services.content import allowed_session_types

        unit.session_types = allowed_session_types(unit, roles)
        session.flush()
        L.apply_schedule(
            session,
            user,
            unit,
            "correct_unaided" if complete and n_ok == len(per_q) else "incorrect",
            today,
            attempt_id=att.id,
            source="assessment",
        )
    arranged = _set_arranged_parts(session, sess)
    conds = sorted(
        {c["kind"] for c in (sess.meta or {}).get("conditions", [])} | ({"arranged_format"} if arranged else set())
    )
    result = {
        **score,
        "slots": {str(s): bool(v) for s, v in sorted(slot_correct.items())},
        "answered": sum(1 for v in normalized_all.values() if v is not None),
        "label": profile["exam"]["label_ja"],
        "kind": profile["exam"]["kind"],
        "mode": sess.mode,
        "profile_id": profile["id"],
        # R05: interrupted / replayed / audio-error runs are kept, but never shown as standard-condition results
        "conditions": conds,
        "standard": not conds,
        # R06: parts that are described in words (形式調整版) keep the set out of standard-condition results
        "arranged_parts": arranged,
        "arranged_detail": _arranged_detail(session, sess, profile),
    }
    sess.status = "submitted"
    sess.submit_hash = h
    sess.result = result
    sess.ended_at = now
    return result


def _set_arranged_parts(session, sess: m.LearningSession) -> list[str]:
    parts = set()
    for it in (sess.meta or {}).get("items", []):
        kind = it.get("presentation")
        if kind is None:  # sets assembled before R06: look at the material itself
            u = session.get(m.Unit, it["unit_id"])
            kind = "official" if u is None or not _arranged(session, u) else "audio_arranged"
        if kind != "official":
            parts.add(it["part"])
    return sorted(parts)


def _arranged_detail(session, sess: m.LearningSession, profile: dict) -> list[dict]:
    """Which questions of this set were not under the exam's conditions, in Japanese, by part and question number."""
    labels = {p["part"]: p["label_ja"] for p in profile["parts"]}
    by: dict[str, list[int]] = {}
    for it in (sess.meta or {}).get("items", []):
        kind = it.get("presentation")
        if kind is None:
            u = session.get(m.Unit, it["unit_id"])
            kind = "official" if u is None or not _arranged(session, u) else "audio_arranged"
        if kind != "official":
            by.setdefault(it["part"], []).extend(int(v) for v in it["slots"].values())
    out = []
    for part, slots in sorted(by.items(), key=lambda kv: min(kv[1])):
        slots = sorted(slots)
        rng = (
            f"問{slots[0]}"
            if len(slots) == 1
            else f"問{slots[0]}〜{slots[-1]}"
            if slots == list(range(slots[0], slots[-1] + 1))
            else "・".join(f"問{x}" for x in slots)
        )
        out.append({"part": part, "slots": slots, "label_ja": f"{labels.get(part, part)}（{rng}）",
                    "note_ja": "絵を文章で示した形式調整版で解答しました。本番は絵を見比べて選びます。"})  # fmt: skip
    return out


def result_dto(session, user: m.User, session_id: str) -> dict:
    sess = get_session(session, user, session_id)
    if sess.status != "submitted":
        raise L.Forbidden("results unlock after the whole assessment is submitted")
    reviews = []
    for it in sess.meta["items"]:
        att = session.get(m.Attempt, sha256_hex(f"{sess.id}:{it['unit_id']}")[:32])
        if att:
            d = L.result_dto(session, att)
            for q in d["questions"]:
                q["global_slot"] = it["slots"][q["id"]]
            reviews.append({"part": it["part"], "section": it["section"], **d})
    return {"session_id": sess.id, "result": sess.result, "units": reviews}


CONDITION_KINDS = ("resume", "replay", "audio_error", "retry")


def record_condition(
    session, user: m.User, session_id: str, kind: str, detail: str = "", now: datetime | None = None
) -> dict:
    """R05: the paper was resumed after an interruption, a section was heard again, or the audio failed and was
    retried. Recorded before submission; the result then says "条件付き" instead of standard conditions."""
    if kind not in CONDITION_KINDS:
        raise L.Invalid("unknown condition")
    sess = get_session(session, user, session_id, lock=True)
    if sess.status != "open":
        return {"conditions": sess.meta.get("conditions", [])}
    now = now or datetime.now(UTC)
    conds = [*(sess.meta or {}).get("conditions", []), {"kind": kind, "detail": detail[:200], "at": now.isoformat()}]
    sess.meta = {**(sess.meta or {}), "conditions": conds[-50:]}
    return {"conditions": sess.meta["conditions"]}
