"""R08: explanations, wrong-choice reasons, translations and glossary get their own checks and audit state."""

from helpers import make_content, ready_unit
from sqlalchemy import select

from lq.db import models as m
from lq.generator.authoring import notes_errors
from lq.services import content as C


def _with_notes(u, translations=None, glossary=None):
    notes = u.study_notes.model_copy(
        update={k: v for k, v in (("translations", translations), ("glossary", glossary)) if v is not None}
    )
    return u.model_copy(update={"study_notes": notes})


def test_structure_errors_and_flags():
    from lq.domain.content import GlossItem, LineTranslation

    u = make_content(part="3", seed="notes")
    u1 = u.segments[0].utterances[0]
    neg = LineTranslation(utterance_id=u1.id, ja=("それは" + ("ある" if " not " in f" {u1.text} " else "ない")) + "。")
    errs, flags = notes_errors(_with_notes(u, translations=[neg]))
    assert any("negation" in f for f in flags)  # the translation flips the negation: flagged for the meaning audit
    dup = [
        LineTranslation(utterance_id=u1.id, ja="一つ目。"),
        LineTranslation(utterance_id=u1.id, ja="二つ目。"),
        LineTranslation(utterance_id="u99", ja="ない行。"),
    ]
    errs, _ = notes_errors(_with_notes(u, translations=dup))
    assert any("twice" in e for e in errs) and any("unknown line u99" in e for e in errs)
    errs, _ = notes_errors(_with_notes(u, glossary=[GlossItem(term="photosynthesis", meaning_ja="光合成")]))
    assert any("does not occur" in e for e in errs)
    q = u.questions[0]
    fb = q.feedback.model_copy(
        update={
            "distractors": [
                *q.feedback.distractors,
                q.feedback.distractors[0].model_copy(update={"choice_id": q.answer.choice_id}),
            ]
        }
    )
    bad = u.model_copy(update={"questions": [q.model_copy(update={"feedback": fb}), *u.questions[1:]]})
    errs, _ = notes_errors(bad)
    assert any("explains the correct choice" in e for e in errs)
    assert notes_errors(u)[0] == []  # old material without notes is accepted


def test_correcting_notes_restarts_only_the_notes_audit(db, user):
    unit = ready_unit(db, content=make_content(part="3", seed="nfix"))
    rec = C.check_notes(db, unit)
    first_hash = rec.content_hash
    C.add_review_record(db, unit, target="notes", method="notes_manual_audited", reviewer="owner", result="pass")
    assert C.notes_audit_state(db, unit)["state"] in ("meaning_audited", "none")
    content_hash, audio = (
        unit.content_hash,
        {a.id for a in db.execute(select(m.Asset).where(m.Asset.unit_id == unit.id)).scalars()},
    )
    u0 = unit.content["segments"][0]["utterances"][0]["id"]
    st = C.update_notes(
        db, unit, {"glossary": [], "translations": [{"utterance_id": u0, "ja": "訳を直しました。"}]}, actor="owner"
    )
    db.commit()
    assert st["state"] in ("structure_ok", "needs_check")  # the earlier meaning audit is not reused
    assert unit.content_hash == content_hash  # grading history stays valid
    assert {
        a.id for a in db.execute(select(m.Asset).where(m.Asset.unit_id == unit.id)).scalars()
    } == audio  # no new audio
    recs = (
        db.execute(select(m.ReviewRecord).where(m.ReviewRecord.unit_id == unit.id, m.ReviewRecord.target == "notes"))
        .scalars()
        .all()
    )
    assert {r.content_hash for r in recs} >= {first_hash} and len({r.content_hash for r in recs}) == 2  # history kept
    assert db.execute(select(m.AuditLog).where(m.AuditLog.action == "notes_updated")).scalars().one()
