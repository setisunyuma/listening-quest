"""Explicit demo providers. They cost nothing and never call the network.

- DemoContent: procedural, self-consistent items (facts stated in the script decide the answers).
- DemoReview: a mock solver. It receives only the learner-visible payload, like a real reviewer, and looks
  the solution up in a private oracle that the demo generator / seeder stored separately.
- DemoTTS: tone bursts per word. This is "音声動作確認用" audio, not usable listening material.
- DemoASR: a mock that echoes the expected text. It verifies plumbing, not audio quality.
Records produced with these are labelled demo_mock_* and are only accepted inside the demo realm.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re

from lq.audio.pcm import BYTES_PER_MS, RATE, silence
from lq.domain.content import UnitContent, canonical_json, reviewer_view, sha256_hex
from lq.domain.profiles import current_profile, part_spec
from lq.generator.authoring import unit_scoring_groups
from lq.generator.providers import GenSpec
from lq.storage import get_storage

NAMES = ["Mika", "Daniel", "Sora", "Elena", "Haruto", "Priya", "Liam", "Aoi", "Noah", "Yuna"]
EVENTS = [
    ("book club", "library", "reading"), ("robotics workshop", "science room", "technology"), ("charity run", "riverside park", "sports"),
    ("cooking class", "community kitchen", "food"), ("photo walk", "old town square", "art"), ("debate practice", "lecture hall", "school"),
    ("garden clean-up", "rooftop garden", "environment"), ("film night", "student lounge", "culture"), ("coding meetup", "computer lab", "technology"),
    ("language exchange", "city cafe", "language"), ("museum tour", "history museum", "culture"), ("volunteer briefing", "town hall", "community"),
]  # fmt: skip
PLACES = [
    "library",
    "science room",
    "riverside park",
    "community kitchen",
    "old town square",
    "lecture hall",
    "rooftop garden",
    "student lounge",
    "computer lab",
    "city cafe",
    "history museum",
    "town hall",
    "gymnasium",
    "music room",
]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
TIMES = ["8:45", "9:15", "9:30", "10:20", "11:40", "1:15", "2:30", "3:45", "4:10", "5:50", "6:30", "7:15"]
PRICES = [5, 8, 12, 15, 18, 25, 30, 40]
FEELINGS = [
    ("excited", "the guest speaker is someone I admire"),
    ("nervous", "I have never tried anything like it"),
    ("doubtful", "the last one was badly organized"),
    ("relieved", "it was finally rescheduled"),
]
STEPS = [
    "introduce ourselves",
    "watch a short demonstration",
    "work in small groups",
    "share our results",
    "fill in a feedback form",
    "tidy up the room",
]
CLUBS = ["drama club", "chess club", "hiking club", "choir", "astronomy club", "pottery club"]
FEEL_JA = {
    "excited": "わくわくしている",
    "nervous": "緊張している",
    "doubtful": "疑っている",
    "relieved": "ほっとしている",
}


def _ordinal(i: int) -> str:
    return ["first", "second", "third", "fourth"][i]


class _Builder:
    def __init__(self, spec: GenSpec):
        self.spec = spec
        self.rng = random.Random(
            int(
                hashlib.sha256(
                    f"{spec.mode}|{spec.part}|{spec.primary_skill}|{spec.band}|{spec.seed}".encode()
                ).hexdigest()[:16],
                16,
            )
        )
        self.profile = current_profile(spec.mode)
        self.part = part_spec(self.profile, spec.part)
        r = self.rng
        self.event, self.place, self.topic = r.choice(EVENTS)
        self.day = r.choice(DAYS)
        self.time, self.old_time = r.sample(TIMES, 2)
        self.price = r.choice(PRICES)
        self.feeling, self.feel_reason = r.choice(FEELINGS)
        self.steps = r.sample(STEPS, 4)
        self.name = r.choice(NAMES)
        n_sp = max(1, self.part["min_speakers"])
        self.speakers = [{"id": "narrator", "role": "narrator", "label": "Narrator", "voice_hint": "neutral"}] + [
            {"id": f"sp{i + 1}", "role": "speaker", "label": chr(65 + i), "voice_hint": "female" if i % 2 else "male"}
            for i in range(n_sp)
        ]
        self.n_sp = n_sp
        self.utts: list[dict] = []
        self.figures: list[dict] = []
        self.makers = {
            "numbers_negation": self.q_time, "explicit_detail": self.q_place, "speaker_attitude": self.q_feeling, "discourse_structure": self.q_second,
            "inference": self.q_inference, "main_idea": self.q_main, "condition_matching": self.q_condition, "integration": self.q_condition,
        }  # fmt: skip

    # ---- script
    def say(self, key: str, text: str) -> str:
        for u in self.utts:
            if u["key"] == key:
                return u["id"]
        uid = f"u{len(self.utts) + 1}"
        self.utts.append(
            {"key": key, "id": uid, "speaker_id": f"sp{len(self.utts) % self.n_sp + 1}", "lang": "en", "text": text}
        )
        return uid

    def _choices(self, correct: str, pool: list[str], n: int = 4) -> tuple[list[dict], str]:
        others = [p for p in pool if p != correct]
        opts = self.rng.sample(others, n - 1) + [correct]
        self.rng.shuffle(opts)
        choices = [{"id": f"c{i + 1}", "text": t} for i, t in enumerate(opts)]
        return choices, next(c["id"] for c in choices if c["text"] == correct)

    def _q(
        self,
        skill: str,
        prompt: str,
        choices,
        cid: str,
        evidence: list[dict],
        answer_en: str,
        ja: str,
        wrong_ja: str,
        figure_ids=(),
    ) -> dict:
        return {
            "prompt_en": prompt, "type": "single_choice", "choices": choices, "items": [], "figure_ids": list(figure_ids),
            "answer": {"type": "single_choice", "choice_id": cid}, "skill_tags": [skill], "evidence": evidence,
            "feedback": {"answer_en": answer_en, "explanation_ja": ja, "hint_ja": "根拠になる一文をもう一度思い出しましょう。",
                         "distractors": [{"choice_id": c["id"], "reason_ja": wrong_ja} for c in choices if c["id"] != cid]},
        }  # fmt: skip

    # ---- question makers (each adds the utterance that decides its answer)
    def q_time(self) -> dict:
        u = self.say("time", f"It starts at {self.time}, not at {self.old_time} as the old poster said.")
        ch, cid = self._choices(
            self.time, [self.time, self.old_time] + [t for t in TIMES if t not in (self.time, self.old_time)][:4]
        )
        if not any(c["text"] == self.old_time for c in ch):  # keep the negated time as a distractor
            wrong = next(c for c in ch if c["id"] != cid)
            wrong["text"] = self.old_time
        return self._q("numbers_negation", f"What time does the {self.event} start?", ch, cid, [{"utterance_id": u}], f"It starts at {self.time}.",
                       f"「{self.time}に始まる。古い掲示の{self.old_time}ではない」と述べています。", "本文で述べられた開始時刻と一致しません。")  # fmt: skip

    def q_place(self) -> dict:
        u = self.say("place", f"The {self.event} will be held in the {self.place} this {self.day}.")
        ch, cid = self._choices(f"In the {self.place}", [f"In the {p}" for p in PLACES])
        return self._q("explicit_detail", f"Where will the {self.event} be held?", ch, cid, [{"utterance_id": u}], f"It will be held in the {self.place}.",
                       f"「{self.place}で行われる」と明言しています。", "本文で述べられた場所ではありません。")  # fmt: skip

    def q_feeling(self) -> dict:
        u = self.say("feeling", f"Honestly, I am {self.feeling} about it because {self.feel_reason}.")
        ch, cid = self._choices(f"The speaker is {self.feeling}.", [f"The speaker is {f}." for f, _ in FEELINGS])
        return self._q("speaker_attitude", f"How does the speaker feel about the {self.event}?", ch, cid, [{"utterance_id": u}], f"The speaker is {self.feeling}.",
                       f"話者は理由を添えて「{FEEL_JA[self.feeling]}」気持ちを述べています。", "話者が述べた気持ちと異なります。")  # fmt: skip

    def q_second(self) -> dict:
        s = self.steps
        u = self.say("steps", f"First we will {s[0]}, then {s[1]}, after that {s[2]}, and finally {s[3]}.")
        ch, cid = self._choices(s[1].capitalize(), [x.capitalize() for x in s])
        return self._q("discourse_structure", "What will the participants do second?", ch, cid, [{"utterance_id": u}], f"Second, they will {s[1]}.",
                       "First, then, after that, finally の順序表現から2番目を特定します。", "2番目の活動ではありません。")  # fmt: skip

    def q_inference(self) -> dict:
        u = self.say(
            "transport",
            f"{self.name} usually comes by bus, but the buses are not running that day, so a neighbor offered to drive.",
        )
        ch, cid = self._choices("By car", ["By car", "By bus", "By bicycle", "On foot", "By train"])
        if not any(c["text"] == "By bus" for c in ch):
            next(c for c in ch if c["id"] != cid)["text"] = "By bus"
        return self._q("inference", f"How will {self.name} probably get to the {self.event}?", ch, cid, [{"utterance_id": u}], "Probably by car.",
                       "バスは運休で、近所の人が車で送ると申し出ているため、車で行くと推測できます。", "本文の状況から導けない移動手段です。")  # fmt: skip

    def q_main(self) -> dict:
        u = self.say("place", f"The {self.event} will be held in the {self.place} this {self.day}.")
        ch, cid = self._choices(
            f"Details of an upcoming {self.event}", [f"Details of an upcoming {e}" for e, _, _ in EVENTS]
        )
        return self._q("main_idea", "What is the talk mainly about?", ch, cid, [{"utterance_id": u}], f"It is about an upcoming {self.event}.",
                       f"全体を通じて{self.event}の案内をしています。", "話題になっている催しではありません。")  # fmt: skip

    def q_condition(self) -> dict:
        r = self.rng
        limit = r.choice([10, 15, 20])
        day = self.day
        other_day = r.choice([d for d in DAYS if d != day])
        plans = ["Plan A", "Plan B", "Plan C", "Plan D"]
        good = r.randrange(4)
        rows = []
        for i, p in enumerate(plans):
            if i == good:
                price, d = limit - r.choice([2, 4]), day
            elif i == (good + 1) % 4:
                price, d = limit + 5, day  # right day, too expensive
            elif i == (good + 2) % 4:
                price, d = limit - 3, other_day  # cheap, wrong day
            else:
                price, d = limit + 8, other_day
            rows.append(
                {
                    "id": f"r{i + 1}",
                    "cells": [
                        {"id": f"r{i + 1}c1", "text": p},
                        {"id": f"r{i + 1}c2", "text": f"${price}"},
                        {"id": f"r{i + 1}c3", "text": d},
                    ],
                }
            )
        fid = f"f{len(self.figures) + 1}"
        self.figures.append(
            {
                "id": fid,
                "kind": "table",
                "title": "Available plans",
                "columns": ["Plan", "Price", "Day"],
                "rows": rows,
                "note": "",
            }
        )
        u = self.say("cond", f"We need a plan that costs less than {limit} dollars and is available on {day}.")
        choices = [{"id": f"c{i + 1}", "text": p} for i, p in enumerate(plans)]
        skill = "integration" if self.spec.primary_skill == "integration" else "condition_matching"
        q = self._q(skill, "Which plan meets the speaker's conditions?", choices, f"c{good + 1}", [{"utterance_id": u}, {"figure_id": fid, "element_id": f"r{good + 1}"}],
                    f"{plans[good]} meets both conditions.", f"{limit}ドル未満かつ{day}に利用できるのは{plans[good]}だけです。", "価格または曜日の条件を満たしません。", figure_ids=[fid])  # fmt: skip
        self.radio_desc = "The table lists four plans. " + " ".join(
            f"{row['cells'][0]['text']} costs {row['cells'][1]['text'][1:]} dollars and is available on {row['cells'][2]['text']}."
            for row in rows
        )
        return q

    # ---- special formats
    def build_ordering(self) -> list[dict]:
        s = self.steps
        u = self.say("steps", f"First we will {s[0]}, then {s[1]}, after that {s[2]}, and finally {s[3]}.")
        opts = [x.capitalize() for x in s]
        order = list(range(4))
        self.rng.shuffle(order)
        choices = [{"id": f"c{i + 1}", "text": opts[j]} for i, j in enumerate(order)]
        qs = []
        for pos in range(4):
            cid = next(c["id"] for c in choices if c["text"] == opts[pos])
            qs.append(self._q("discourse_structure", f"Which activity comes {_ordinal(pos)}?", [dict(c) for c in choices], cid, [{"utterance_id": u}],
                              f"{opts[pos]} comes {_ordinal(pos)}.", f"順序表現から{pos + 1}番目の活動を特定します。", f"{pos + 1}番目の活動ではありません。"))  # fmt: skip
        return qs

    def build_matching(self) -> list[dict]:
        names = self.rng.sample(NAMES, 4)
        clubs = self.rng.sample(CLUBS, 4)
        choices = [{"id": f"c{i + 1}", "text": c.capitalize()} for i, c in enumerate(CLUBS)]
        qs = []
        for n, c in zip(names, clubs, strict=True):
            u = self.say(f"club-{n}", f"{n} signed up for the {c}.")
            cid = next(x["id"] for x in choices if x["text"] == c.capitalize())
            qs.append(self._q("explicit_detail", f"Which club did {n} sign up for?", [dict(x) for x in choices], cid, [{"utterance_id": u}],
                              f"{n} signed up for the {c}.", f"{n}は{c}に申し込んだと述べています。", f"{n}が申し込んだクラブではありません。"))  # fmt: skip
        return qs

    def build(self) -> dict:
        return self._conform(self._build())

    def _conform(self, d: dict) -> dict:
        """R06: make demo material satisfy the profile's format contract (choice counts, shared choices, pictures
        as an arranged presentation, required figures, part-5 section roles and slot evidence). Demo content is
        only a working stand-in; real material is checked against the same contract by lint."""
        c = self.part.get("contract") or {}
        qs = d["questions"]
        n = c.get("choices")
        fillers = [
            "None of the above",
            "It was not mentioned",
            "Something else",
            "Nobody said",
            "No answer given",
            "Not decided yet",
        ]
        if c.get("shared_choices") and qs:
            texts = []
            for q in qs:
                for ch in q["choices"]:
                    if ch["text"] not in texts:
                        texts.append(ch["text"])
            for f in fillers:
                if n and len(texts) >= n:
                    break
                texts.append(f)
            shared = [{"id": f"c{i + 1}", "text": t} for i, t in enumerate(texts[: n or len(texts)])]
            for q in qs:
                right = next(ch["text"] for ch in q["choices"] if ch["id"] == q["answer"]["choice_id"])
                q["choices"] = [dict(x) for x in shared]
                q["answer"]["choice_id"] = next(x["id"] for x in shared if x["text"] == right)
                q["feedback"]["distractors"] = [
                    {"choice_id": x["id"], "reason_ja": "正しい選択肢ではありません。"}
                    for x in shared
                    if x["text"] != right
                ]
        elif n:
            for q in qs:
                if q["type"] != "single_choice":
                    continue
                right = q["answer"]["choice_id"]
                keep = [ch for ch in q["choices"] if ch["id"] == right] + [
                    ch for ch in q["choices"] if ch["id"] != right
                ]
                keep = keep[:n]
                for f in fillers:
                    if len(keep) >= n:
                        break
                    if f not in [k["text"] for k in keep]:
                        keep.append({"id": f"cx{len(keep)}", "text": f})
                order = sorted(keep, key=lambda ch: ch["id"])
                q["choices"] = order
                q["feedback"]["distractors"] = [
                    {"choice_id": ch["id"], "reason_ja": "正しい選択肢ではありません。"}
                    for ch in order
                    if ch["id"] != right
                ]
        if c.get("requires_pictures"):
            d["presentation_profile"] = {**d["presentation_profile"], "kind": "audio_arranged", "figure_verbalized": True,
                                         "notes": "demo: pictures described in words (形式調整版)"}  # fmt: skip
        need = c.get("requires_figure")
        if need and not any(f["kind"] == need for f in d["figures"]):
            d["figures"].append({"id": f"f{len(d['figures']) + 1}", "kind": need, "title": "Notes", "columns": ["Item", "Detail"],
                                 "rows": [{"id": "rw1", "cells": [{"id": "rw1c1", "text": "Topic"}, {"id": "rw1c2", "text": self.event}]}], "note": ""})  # fmt: skip
        roles = c.get("segment_roles")
        if roles and len(d["segments"]) == len(roles):
            for seg, role in zip(d["segments"], roles, strict=True):
                seg["label"] = role
            by = {s["label"]: s for s in d["segments"]}
            fig = next((f for f in d["figures"] if f["kind"] == need), d["figures"][0] if d["figures"] else None)
            for q in qs:
                rule = (c.get("slot_rules") or {}).get(str(q["slot"]))
                if not rule:
                    continue
                ev = [e for e in q["evidence"] if "figure_id" in e]
                for need_seg in rule.get("needs_segments", []):
                    utts = by[need_seg]["utterances"]
                    take = utts[:1]
                    if need_seg == "comments" and rule.get("min_comment_speakers"):
                        seen, take = set(), []
                        for u in utts:
                            if u["speaker_id"] not in seen:
                                seen.add(u["speaker_id"])
                                take.append(u)
                    ev += [{"utterance_id": u["id"]} for u in take]
                if rule.get("needs_figure") and fig and not any("figure_id" in e for e in ev):
                    ev.append({"figure_id": fig["id"], "element_id": fig["rows"][0]["id"] if fig["rows"] else None})
                    q["figure_ids"] = sorted(set(q.get("figure_ids", [])) | {fig["id"]})
                q["evidence"] = [{k: v for k, v in e.items() if v is not None} for e in ev]
                q["segment_ids"] = sorted(
                    {
                        s["id"]
                        for s in d["segments"]
                        for u in s["utterances"]
                        for e in q["evidence"]
                        if e.get("utterance_id") == u["id"]
                    }
                )
        return d

    def _build(self) -> dict:
        spec, part = self.spec, self.part
        self.radio_desc = ""
        n = part["questions_per_unit"]
        self.say("intro", f"{self.name} here with an update for group {self.rng.randint(100, 999)}.")
        self.say("place", f"The {self.event} will be held in the {self.place} this {self.day}.")
        if part["template_type"] == "ordering":
            qs = self.build_ordering()
        elif part["template_type"] == "matching":
            qs = self.build_matching()
        else:
            order = [spec.primary_skill] + [
                k
                for k in (
                    "numbers_negation",
                    "explicit_detail",
                    "speaker_attitude",
                    "discourse_structure",
                    "inference",
                    "main_idea",
                    "condition_matching",
                )
                if k != spec.primary_skill
            ]
            if spec.primary_skill == "integration":
                order = [k for k in order if k != "condition_matching"]
            qs = [self.makers[k]() for k in order[:n]]
        while len({u["speaker_id"] for u in self.utts}) < self.n_sp:  # make sure every required speaker talks
            self.say(f"filler{len(self.utts)}", f"Thanks for telling me. I will mark {self.day} on my calendar.")
        n_seg = part.get("segments", 1)
        size = math.ceil(len(self.utts) / n_seg)
        segments = []
        for i in range(n_seg):
            chunk = self.utts[i * size : (i + 1) * size] or [self.utts[-1]]
            segments.append(
                {
                    "id": f"s{i + 1}",
                    "label": "",
                    "plays": part["plays"],
                    "utterances": [{k: v for k, v in u.items() if k != "key"} for u in chunk],
                }
            )
        seg_of = {u["id"]: s["id"] for s in segments for u in s["utterances"]}
        questions = []
        for i, q in enumerate(qs):
            segs = sorted({seg_of[e["utterance_id"]] for e in q["evidence"] if "utterance_id" in e})
            questions.append(
                {
                    "id": f"q{i + 1}",
                    "slot": i + 1,
                    "segment_ids": segs if n_seg > 1 else [],
                    "prompt_medium": part["question_medium"],
                    "prompt_timing": part["question_timing"],
                    **q,
                }
            )
        skills = sorted({s for q in questions for s in q["skill_tags"]})
        pre = None
        if part["template_type"] == "real_life":
            pre = {
                "situation_en": f"You want to join the {self.event} and you are deciding whether you can go. You hear the following announcement.",
                "read_seconds": part.get("pre_read_sec", 10),
            }
        tag = hashlib.sha256(f"{spec.seed}".encode()).hexdigest()[:10]
        return {
            "schema_version": 2, "source": spec.source, "content_id": f"demo-{spec.mode}-{spec.part}-{tag}".replace("_", "-"), "revision": 1,
            "mode": spec.mode, "profile_version": self.profile["id"], "part": spec.part, "template_type": part["template_type"],
            "title": f"デモ教材 {part['label_ja']} #{tag[:4]}", "topic": self.topic, "skill_tags": skills,
            "primary_skill": spec.primary_skill if spec.primary_skill in skills else skills[0],
            "provisional_difficulty_band": spec.band, "calibration_status": "provisional",
            "family_id": f"demo:{spec.mode}:{spec.part}:{self.event.replace(' ', '-')}:{tag[:6]}", "variant_of": None,
            "presentation_profile": {"kind": "official", "choices_read_aloud": False, "figure_verbalized": False, "notes": "demo"},
            "speakers": self.speakers, "pre_info": pre, "figures": self.figures, "segments": segments, "questions": questions,
            "scoring_groups": unit_scoring_groups(self.profile, spec.part, [q["id"] for q in questions]),
            "radio": {"supported": True, "reason": "", "figure_description_en": self.radio_desc},
            "study_notes": {
                "glossary": [{"term": self.event, "meaning_ja": "（デモ用の語注）この教材の話題になっている行事"}],
                "translations": [{"utterance_id": u["id"], "ja": "（デモ用の仮訳）" + u["text"][:60]}
                                 for seg in segments for u in seg["utterances"]][:20],
            },
        }  # fmt: skip


def oracle_key(payload: dict) -> str:
    return f"demo/oracle/{sha256_hex(canonical_json(payload))}.json"


def register_demo_oracle(unit: UnitContent) -> None:
    """Store the mock reviewer's private 'knowledge' for a demo unit (separate from the review payload)."""
    answers = {}
    for q in unit.questions:
        a = q.answer.model_dump()
        a.pop("type")
        if "accepted" in a:
            a = {"text": a["accepted"][0]}
        answers[q.id] = a
    get_storage().put(oracle_key(reviewer_view(unit)), json.dumps(answers).encode(), "application/json")


class DemoContent:
    name = "demo"
    model = "demo-procedural-v1"

    def generate(self, spec: GenSpec, meter) -> dict:
        data = _demo_practice(spec) if spec.practice_kind else _Builder(spec).build()
        register_demo_oracle(UnitContent.model_validate(data))
        return data


class DemoReview:
    name = "demo"
    model = "demo-mock-solver-v1"
    record_method = "demo_mock_reviewed"

    def __init__(self):
        self.seen_payloads: list[dict] = []  # tests inspect this to prove nothing leaks

    def solve(self, payload: dict, meter) -> dict:
        self.seen_payloads.append(payload)
        st = get_storage()
        key = oracle_key(payload)
        if not st.exists(key):
            return {
                "unavailable": True,
                "answers": {},
                "ambiguous": [],
                "notes": "demo mock: this material was not produced by the demo generator, so the mock cannot review it",
            }
        return {
            "answers": json.loads(st.get(key)),
            "ambiguous": [],
            "notes": "demo mock solver (not an independent review)",
        }


def _tone(freq: float, ms: int, amp: float = 0.28) -> bytes:
    n_cycle = max(8, round(RATE / freq))
    cycle = b"".join(
        int(amp * 32767 * math.sin(2 * math.pi * i / n_cycle)).to_bytes(2, "little", signed=True)
        for i in range(n_cycle)
    )
    need = ms * BYTES_PER_MS
    return (cycle * (need // len(cycle) + 1))[: need // 2 * 2]


class DemoTTS:
    name = "demo"
    model = "demo-tone-v1"
    audio_kind = "demo_tone"

    def synthesize(self, text: str, voice: str, lang: str, instructions: str, meter) -> bytes:
        base = 170 + int(hashlib.sha256(voice.encode()).hexdigest()[:4], 16) % 260  # one pitch per voice
        words = text.split() if lang == "en" else [text[i : i + 3] for i in range(0, len(text), 3)]
        parts = []
        for i, w in enumerate(words):
            parts.append(_tone(base * (1.0 + 0.06 * ((len(w) + i) % 3)), min(60 + 45 * len(w), 420)))
            parts.append(silence(70))
        return b"".join(parts) or silence(200)


class DemoASR:
    name = "demo"
    model = "demo-mock-asr-v1"
    record_method = "audio_demo_mock_checked"

    def transcribe(self, pcm: bytes, lang: str, meter, expected_hint: str = "") -> str:
        return expected_hint


def _demo_practice(spec: GenSpec) -> dict:
    """Demo realm only: a practice task built from a bundled example of the same kind, made distinct by the seed
    (a marked demo variation, never presented as new real material)."""
    import hashlib

    import yaml

    from lq.config import get_settings
    from lq.services.seed import expand_fixture

    raws = [
        r
        for r in yaml.safe_load((get_settings().fixtures_dir / "units" / "practice.yaml").read_text(encoding="utf-8"))
        if r["part"] == spec.practice_kind
    ]
    if not raws:
        raise ValueError(f"no demo example for {spec.practice_kind}")
    raw = dict(raws[int(hashlib.sha256(spec.seed.encode()).hexdigest(), 16) % len(raws)])
    tag = re.sub(r"[^a-z0-9]+", "-", spec.seed.lower()).strip("-")[:24] or "x"
    raw["id"] = f"{raw['id']}-demo-{tag}"
    raw["family"] = f"{raw['family']}-demo-{tag}"
    script = [list(seg) for seg in raw["script"]]
    first = script[0][0]
    script[0][0] = f"{first} (Demo variation {tag}.)" if not first.rstrip().endswith(".)") else first
    raw["script"] = script
    data = expand_fixture(raw)
    data["source"] = spec.source
    return data
