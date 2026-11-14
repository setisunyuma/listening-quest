"""E01 / E02 (V01, V02): Eiken Pre-1 / Grade 1 listening under the official conditions of the 2026-2 test."""

from helpers import make_content

from lq.audio import render as R
from lq.domain.content import Speaker, Utterance, learner_view
from lq.domain.profiles import current_profile, part_spec, total_slots
from lq.generator.authoring import lint_unit
from lq.generator.demo import _tone


def test_v01_question_counts_one_play_and_ten_second_answers():
    for mode, n, parts in (
        ("eiken_pre1", 29, ["part1", "part2", "part3"]),
        ("eiken_1", 27, ["part1", "part2", "part3", "part4"]),
    ):
        p = current_profile(mode)
        assert total_slots(p) == n and [x["part"] for x in p["parts"]] == parts
        assert p["exam"]["answer_time_min"] is None  # the guide's "about 30 / 35 minutes" never ends a run
        for part in p["parts"]:
            assert part["plays"] == 1 and part["answer_wait_sec"] == 10
            if part["part"] == "part3":  # Real-Life: Situation / Question / choices on screen, 10 s to read first
                assert (
                    part["question_medium"] == "screen"
                    and part["question_timing"] == "before"
                    and part["pre_read_sec"] == 10
                )
            else:  # the question is heard once; no question text on screen while answering
                assert part["question_medium"] == "audio" and part["pre_read_sec"] == 0
                u = make_content(mode=mode, part=part["part"], seed=f"v01-{mode}-{part['part']}")
                view = learner_view(u)
                assert all(q["prompt_en"] is None for q in view["questions"])
                assert all(len(q["choices"]) == 4 for q in view["questions"])
                # showing the text is an explicit, recorded aid only
                assert all(q["prompt_en"] for q in learner_view(u, show_audio_prompts=True)["questions"])


def _exam_timeline(mode, part):
    u = make_content(mode=mode, part=part, seed=f"tl-{mode}-{part}")
    clips = {cid: _tone(200 + 7 * i, 200) for i, cid in enumerate(R.plan_clips(u))}
    prof = current_profile(mode)
    return u, R.render_exam(u, clips, part_spec(prof, part), prof.get("timing", {})).timeline


def test_v02_passage_and_interview_are_played_once_then_q1_wait_q2_wait():
    for mode, part in (("eiken_pre1", "part2"), ("eiken_1", "part2"), ("eiken_1", "part4")):
        u, tl = _exam_timeline(mode, part)
        utts = [e for e in tl if e["kind"] == "utterance"]
        assert len(utts) == sum(len(s.utterances) for s in u.segments)  # the body is not replayed per question
        seq = [e["kind"] for e in tl if e["kind"] in ("utterance", "prompt", "answer_wait")]
        tail = seq[len(utts) :]
        assert tail == ["prompt", "answer_wait", "prompt", "answer_wait"], (mode, part, tail)  # never one 20 s wait
        waits = [e for e in tl if e["kind"] == "answer_wait"]
        assert all(e["end_ms"] - e["start_ms"] == 10_000 for e in waits)


def test_v01_real_life_reads_for_ten_seconds_before_the_audio():
    for mode in ("eiken_pre1", "eiken_1"):
        _, tl = _exam_timeline(mode, "part3")
        assert tl[0]["kind"] == "pre_read_wait" and tl[0]["end_ms"] - tl[0]["start_ms"] == 10_000
        assert not [e for e in tl if e["kind"] == "prompt"]  # the question is on screen, not read out


def test_v02_grade1_part1_accepts_three_speakers_with_attribution():
    u = make_content(mode="eiken_1", part="part1", seed="three")
    third = Speaker(id="sp3", role="speaker", label="C", voice_hint="female")
    seg = u.segments[0]
    extra = Utterance(id="u90", speaker_id="sp3", lang="en", text="Count me in, but I can only stay until noon.")
    u3 = u.model_copy(
        update={
            "speakers": [*u.speakers, third],
            "segments": [seg.model_copy(update={"utterances": [*seg.utterances, extra]}), *u.segments[1:]],
        }
    )
    assert lint_unit(u3) == []
    assert {x.speaker_id for x in u3.segments[0].utterances} == {"sp1", "sp2", "sp3"}
    # an utterance by a speaker that is not declared is rejected (speaker attribution is checked)
    ghost = Utterance(id="u91", speaker_id="sp9", lang="en", text="Hello.")
    bad = u.model_copy(update={"segments": [seg.model_copy(update={"utterances": [*seg.utterances, ghost]})]})
    import pytest
    from pydantic import ValidationError

    from lq.domain.content import UnitContent

    with pytest.raises(ValidationError, match="unknown speaker|speaker"):
        UnitContent.model_validate(bad.model_dump())
