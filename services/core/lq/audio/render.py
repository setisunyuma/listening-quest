"""Render the audio tracks of a unit from per-clip PCM.

Tracks (kept strictly apart):
- stimulus:<segment>  what may be heard before answering (passage + audio-only prompts), one play
- exam                official timed presentation incl. repeats and answer waits (assessment)
- feedback            correct answers, evidence (original utterance audio reused), Japanese explanation
- radio               continuous listen-through: preview, passage, questions, silent wait, answers, explanation
- utt:<utterance>     single utterances, used for evidence replay after feedback is unlocked
"""

from __future__ import annotations

from dataclasses import dataclass

from lq.audio.pcm import Track
from lq.domain.content import Question, UnitContent

RENDER_VERSION = 1
GAP_UTT = 350
GAP_BLOCK = 900
RADIO_ANSWER_WAIT_MS = 6000


@dataclass(frozen=True)
class ClipSpec:
    clip_id: str
    text: str
    lang: str
    speaker_id: str
    role: str  # utterance | narration | explanation
    required_for: tuple[str, ...]  # track kinds needing this clip


def radio_supported(unit: UnitContent) -> tuple[bool, str]:
    if not unit.radio.supported:
        return False, unit.radio.reason or "author marked as not suitable for audio-only"
    if any(q.type != "single_choice" for q in unit.questions):
        return False, "ordering/matching/blank questions are not narrated; use a radio alternative"
    if unit.figures and not unit.radio.figure_description_en:
        return False, "figure-dependent unit without an audio description"
    return True, ""


def _attached(unit: UnitContent, seg_id: str) -> list[Question]:
    last = unit.segments[-1].id
    return [q for q in unit.sorted_questions() if (seg_id in q.segment_ids) or (not q.segment_ids and seg_id == last)]


def _primary_segment(unit: UnitContent, q: Question) -> str:
    """The last segment a question depends on: it is presented once that segment has been heard."""
    order = [s.id for s in unit.segments]
    if not q.segment_ids:
        return order[-1]
    return max(q.segment_ids, key=order.index)


def _presented_after(unit: UnitContent, seg_id: str) -> list[Question]:
    return [q for q in unit.sorted_questions() if _primary_segment(unit, q) == seg_id]


def choice_number(q: Question, choice_id: str) -> int:
    return [c.id for c in q.choices].index(choice_id) + 1


def plan_clips(unit: UnitContent) -> dict[str, ClipSpec]:
    """Every clip needed to render all tracks of this unit."""
    narr = next((s.id for s in unit.speakers if s.role == "narrator"), "narrator")
    clips: dict[str, ClipSpec] = {}
    for seg in unit.segments:
        for u in seg.utterances:
            clips[f"utt:{u.id}"] = ClipSpec(
                f"utt:{u.id}", u.text, u.lang, u.speaker_id, "utterance", ("stimulus", "exam", "feedback", "radio")
            )
    radio_ok, _ = radio_supported(unit)
    for q in unit.sorted_questions():
        audio_prompt = q.prompt_medium in ("audio", "both")
        need = tuple(k for k, on in (("stimulus", audio_prompt), ("exam", audio_prompt), ("radio", radio_ok)) if on)
        if need:
            clips[f"prompt:{q.id}"] = ClipSpec(
                f"prompt:{q.id}", f"Question {q.slot}. {q.prompt_en}", "en", narr, "narration", need
            )
        if q.type == "single_choice":
            n = choice_number(q, q.answer.choice_id)
            text = next(c.text for c in q.choices if c.id == q.answer.choice_id)
            ans = f"Question {q.slot}. The answer is number {n}. {text}"
            if radio_ok:
                listed = " ".join(f"Number {i}. {c.text}." for i, c in enumerate(q.choices, 1))
                clips[f"choices:{q.id}"] = ClipSpec(f"choices:{q.id}", listed, "en", narr, "narration", ("radio",))
        else:
            ans = f"Question {q.slot}. {q.feedback.answer_en}"
        clips[f"answer:{q.id}"] = ClipSpec(f"answer:{q.id}", ans, "en", narr, "narration", ("feedback", "radio"))
        clips[f"explain:{q.id}"] = ClipSpec(
            f"explain:{q.id}", q.feedback.explanation_ja, "ja", "explainer_ja", "explanation", ("feedback", "radio")
        )
    if radio_ok:
        if unit.pre_info and unit.pre_info.situation_en:
            clips["pre"] = ClipSpec(
                "pre", f"Situation. {unit.pre_info.situation_en}", "en", narr, "narration", ("radio",)
            )
        if unit.figures and unit.radio.figure_description_en:
            clips["figdesc"] = ClipSpec(
                "figdesc", unit.radio.figure_description_en, "en", narr, "narration", ("radio",)
            )
    return clips


def _segment_block(track: Track, unit: UnitContent, seg, clips: dict[str, bytes], with_prompts: bool) -> None:
    for i, u in enumerate(seg.utterances):
        if i:
            track.gap(GAP_UTT)
        track.add("utterance", clips[f"utt:{u.id}"], ref=u.id, segment=seg.id)
    if with_prompts:
        for q in _presented_after(unit, seg.id):
            if q.prompt_medium in ("audio", "both"):
                track.gap(GAP_BLOCK)
                track.add("prompt", clips[f"prompt:{q.id}"], ref=q.id)


def render_stimulus(unit: UnitContent, seg_id: str, clips: dict[str, bytes]) -> Track:
    seg = next(s for s in unit.segments if s.id == seg_id)
    t = Track()
    _segment_block(t, unit, seg, clips, with_prompts=True)
    t.gap(300)
    return t


def render_exam(unit: UnitContent, clips: dict[str, bytes], part: dict, timing: dict) -> Track:
    """Official presentation for assessments: pre-read wait, plays, audio prompts, answer waits."""
    t = Track()
    pre = max(part.get("pre_read_sec", 0), unit.pre_info.read_seconds if unit.pre_info else 0)
    t.gap(pre * 1000, "pre_read_wait")
    wait_ms = int(part.get("answer_wait_sec", 10) * 1000)
    for seg in unit.segments:
        audio_qs = [q for q in _presented_after(unit, seg.id) if q.prompt_medium in ("audio", "both")]
        screen_qs = [q for q in _presented_after(unit, seg.id) if q.prompt_medium == "screen"]
        if seg.plays > 1:
            for i in range(seg.plays):
                if i:
                    t.gap(int(timing.get("between_plays_sec", 2) * 1000) or GAP_BLOCK, "between_plays")
                _segment_block(t, unit, seg, clips, with_prompts=True)
            if audio_qs or screen_qs:
                t.gap(wait_ms, "answer_wait")
        else:
            _segment_block(t, unit, seg, clips, with_prompts=False)
            for q in audio_qs:  # passage -> Q1 -> wait -> Q2 -> wait
                t.gap(GAP_BLOCK)
                t.add("prompt", clips[f"prompt:{q.id}"], ref=q.id)
                t.gap(wait_ms, "answer_wait", ref=q.id)
            if screen_qs:
                t.gap(wait_ms, "answer_wait")
        t.gap(GAP_BLOCK)
    return t


def _feedback_block(t: Track, unit: UnitContent, q: Question, clips: dict[str, bytes]) -> None:
    t.add("answer", clips[f"answer:{q.id}"], ref=q.id)
    for ev in q.evidence:
        if ev.utterance_id:  # whole original utterance; no guessed timestamps
            t.gap(GAP_UTT)
            t.add("evidence", clips[f"utt:{ev.utterance_id}"], ref=ev.utterance_id, question=q.id)
    t.gap(GAP_UTT)
    t.add("explanation", clips[f"explain:{q.id}"], ref=q.id)
    t.gap(GAP_BLOCK)


def render_feedback(unit: UnitContent, clips: dict[str, bytes]) -> Track:
    t = Track()
    for q in unit.sorted_questions():
        _feedback_block(t, unit, q, clips)
    return t


def render_radio(unit: UnitContent, clips: dict[str, bytes]) -> Track:
    """Preview -> passage (official plays) -> questions and choices again -> silent wait -> ... -> answers."""
    t = Track()
    if "pre" in clips:
        t.add("pre_info", clips["pre"])
        t.gap(GAP_BLOCK)
    if "figdesc" in clips:
        t.add("figure_description", clips["figdesc"])
        t.gap(GAP_BLOCK)

    def present(q: Question) -> None:
        t.add("prompt", clips[f"prompt:{q.id}"], ref=q.id)
        t.gap(GAP_UTT)
        t.add("choices", clips[f"choices:{q.id}"], ref=q.id)
        t.gap(GAP_BLOCK)

    for seg in unit.segments:
        qs = _presented_after(unit, seg.id)
        for q in qs:
            present(q)
        for i in range(seg.plays):
            if i:
                t.gap(GAP_BLOCK, "between_plays")
            _segment_block(t, unit, seg, clips, with_prompts=False)
        t.gap(GAP_BLOCK)
        for q in qs:
            present(q)
            t.gap(RADIO_ANSWER_WAIT_MS, "answer_wait", ref=q.id)
    for q in unit.sorted_questions():
        _feedback_block(t, unit, q, clips)
    return t


def transcript_of(track: Track, unit: UnitContent, specs: dict[str, ClipSpec]) -> list[dict]:
    """Server-side transcript aligned with the timeline (never sent to answer-type sessions)."""
    utt = unit.utterance_map()
    out = []
    for e in track.timeline:
        ref = e.get("ref")
        if e["kind"] in ("utterance", "evidence") and ref in utt:
            out.append(
                {
                    "start_ms": e["start_ms"],
                    "end_ms": e["end_ms"],
                    "kind": e["kind"],
                    "speaker": utt[ref].speaker_id,
                    "text": utt[ref].text,
                }
            )
        elif e["kind"] in ("prompt", "choices", "answer", "explanation"):
            cid = {"explanation": "explain"}.get(e["kind"], e["kind"]) + f":{ref}"
            if cid in specs:
                out.append(
                    {"start_ms": e["start_ms"], "end_ms": e["end_ms"], "kind": e["kind"], "text": specs[cid].text}
                )
        elif e["kind"] in ("pre_info", "figure_description"):
            cid = "pre" if e["kind"] == "pre_info" else "figdesc"
            out.append({"start_ms": e["start_ms"], "end_ms": e["end_ms"], "kind": e["kind"], "text": specs[cid].text})
    return out
