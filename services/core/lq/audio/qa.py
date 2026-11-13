"""Audio checks: decode / length / emptiness / level, and transcript comparison for independent ASR.

`compare_transcript` separates notation differences (case, punctuation, "ten" vs "10", contractions)
from meaning differences (a missing number or negation is always a failure)."""

from __future__ import annotations

import re

from lq.audio.pcm import AudioError, decode_to_pcm, duration_ms, peak_ratio, probe, rms_dbfs

_NUM = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90",
}  # fmt: skip
_NEG = {"not", "no", "never", "neither", "nor", "none", "nobody", "nothing", "without", "hardly", "barely", "rarely", "seldom", "cannot"}
_CONTR = [(r"\bcan't\b", "can not"), (r"\bwon't\b", "will not"), (r"\bshan't\b", "shall not"), (r"n't\b", " not"), (r"\bcannot\b", "can not")]


def normalize_tokens(text: str) -> list[str]:
    t = text.lower().replace("’", "'")
    for pat, rep in _CONTR:
        t = re.sub(pat, rep, t)
    t = re.sub(r"(\d),(\d{3})", r"\1\2", t)
    t = re.sub(r"[^a-z0-9'\s:.]", " ", t)
    toks = []
    for raw in t.split():
        w = raw.strip(".':")
        if not w:
            continue
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", w)
        if m:
            toks += [str(int(m.group(1))), str(int(m.group(2)))] if int(m.group(2)) else [str(int(m.group(1)))]
            continue
        w = w.replace("'", "")
        if "-" in w:
            w = w.replace("-", " ")
        for part in w.split():
            toks.append(_NUM.get(part, part))
    # merge "20 5" -> "25" so "twenty-five" equals "25"
    out: list[str] = []
    for tok in toks:
        if out and tok.isdigit() and len(tok) == 1 and out[-1].isdigit() and len(out[-1]) == 2 and out[-1].endswith("0") and out[-1] != "10":
            out[-1] = str(int(out[-1]) + int(tok))
        else:
            out.append(tok)
    return out


def _wer(ref: list[str], hyp: list[str]) -> float:
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


def critical_tokens(tokens: list[str]) -> list[str]:
    return sorted(t for t in tokens if t.isdigit() or t in _NEG)


def compare_transcript(expected: str, heard: str, *, is_evidence: bool = False, max_wer: float = 0.2, max_wer_evidence: float = 0.1) -> dict:
    ref, hyp = normalize_tokens(expected), normalize_tokens(heard)
    wer = _wer(ref, hyp)
    crit_ref, crit_hyp = critical_tokens(ref), critical_tokens(hyp)
    limit = max_wer_evidence if is_evidence else max_wer
    problems = []
    if crit_ref != crit_hyp:
        problems.append({"type": "critical_token_mismatch", "expected": crit_ref, "heard": crit_hyp})
    if wer > limit:
        problems.append({"type": "wer_too_high", "wer": round(wer, 3), "limit": limit})
    return {"ok": not problems, "wer": round(wer, 3), "problems": problems}


def check_encoded(data: bytes, expected_ms: int, *, expect_sound: bool = True) -> dict:
    """Decode the final file and verify it is real, complete, audible audio."""
    problems: list[str] = []
    info: dict = {}
    try:
        info = probe(data)
        pcm = decode_to_pcm(data)
    except AudioError as e:
        return {"ok": False, "problems": [f"decode_failed: {e}"]}
    got = duration_ms(pcm)
    if abs(got - expected_ms) > max(400, expected_ms * 0.03):
        problems.append(f"duration_mismatch: expected {expected_ms}ms got {got}ms")
    level = rms_dbfs(pcm)
    if expect_sound and level < -55:
        problems.append(f"silent_audio: {level:.1f} dBFS")
    if peak_ratio(pcm) >= 0.999 and level > -3:
        problems.append("clipping")
    return {"ok": not problems, "problems": problems, "duration_ms": got, "rms_dbfs": round(level, 1), "codec": info.get("codec")}


def check_clip(pcm: bytes, text: str) -> list[str]:
    problems = []
    ms = duration_ms(pcm)
    words = max(1, len(text.split()) if " " in text else len(text) // 3)
    if ms < 150:
        problems.append("clip_too_short")
    if ms > 60_000 + words * 1500:
        problems.append("clip_too_long")
    if rms_dbfs(pcm) < -55:
        problems.append("clip_silent")
    return problems
