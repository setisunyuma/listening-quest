"""Audio checks: decode / length / emptiness / level, and transcript comparison for independent ASR.

`compare_transcript` separates notation differences (case, punctuation, number spelling, contractions;
see lq/audio/textnorm.py) from meaning differences (a changed or missing number or negation always fails)."""

from __future__ import annotations

from lq.audio.pcm import AudioError, decode_to_pcm, duration_ms, peak_ratio, rms_dbfs
from lq.audio.textnorm import critical_tokens, normalize_tokens  # noqa: E402,F401  (re-exported)


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


def compare_transcript(
    expected: str, heard: str, *, is_evidence: bool = False, max_wer: float = 0.2, max_wer_evidence: float = 0.1
) -> dict:
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
    try:
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
    return {"ok": not problems, "problems": problems, "duration_ms": got, "rms_dbfs": round(level, 1)}


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
