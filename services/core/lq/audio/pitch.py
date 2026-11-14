"""Speaking-pitch estimate for a synthesised clip, used to check that a male speaker is not read by a high voice
and a female speaker is not read by a low one (speech recognition cannot hear that).

Plain Python (no numpy): the clip is averaged down to 8 kHz and at most `max_frames` voiced 40 ms frames are
analysed with normalised autocorrelation, which takes well under a second for a typical line."""

from __future__ import annotations

from array import array
from statistics import median

from lq.audio.pcm import RATE

SR = 8000
FRAME = 320  # 40 ms at 8 kHz
LAG_MIN, LAG_MAX = SR // 400, SR // 60  # 400 Hz .. 60 Hz

# Typical adult speaking pitch: men about 85-155 Hz, women about 165-255 Hz. The limits below only flag a clear
# mismatch, so ordinary variation (a deep female voice, a light male voice) is never rejected.
MALE_MAX_HZ = 175.0
FEMALE_MIN_HZ = 150.0


def _downsample(pcm: bytes) -> list[float]:
    s = array("h")
    s.frombytes(pcm[: len(pcm) // 2 * 2])
    k = RATE // SR
    return [sum(s[i : i + k]) / k for i in range(0, len(s) - k + 1, k)]


def median_f0(pcm: bytes, max_frames: int = 60) -> float | None:
    """Median fundamental frequency (Hz) over voiced frames, or None when there is too little voiced speech."""
    x = _downsample(pcm)
    frames = [x[i : i + FRAME] for i in range(0, len(x) - FRAME - LAG_MAX, FRAME)]
    if not frames:
        return None
    energy = [sum(v * v for v in f) for f in frames]
    top = max(energy)
    if top <= 0:
        return None
    loud = [i for i, e in enumerate(energy) if e >= 0.05 * top]
    if len(loud) > max_frames:
        step = len(loud) / max_frames
        loud = [loud[int(j * step)] for j in range(max_frames)]
    f0s = []
    for i in loud:
        start = i * FRAME
        seg = x[start : start + FRAME + LAG_MAX]
        mean = sum(seg) / len(seg)
        seg = [v - mean for v in seg]
        base = seg[:FRAME]
        r0 = sum(v * v for v in base)
        if r0 <= 0:
            continue
        rs = []
        for lag in range(LAG_MIN, LAG_MAX + 1):
            shifted = seg[lag : lag + FRAME]
            r = sum(a * b for a, b in zip(base, shifted, strict=False))
            e2 = sum(v * v for v in shifted)
            rs.append(r / ((r0 * e2) ** 0.5) if e2 > 0 else 0.0)
        best = max(rs)
        if best < 0.6:
            continue  # unvoiced or noisy frame
        # the shortest lag that is nearly as good as the best avoids halving the pitch (octave errors)
        lag = next(LAG_MIN + j for j, r in enumerate(rs) if r >= 0.9 * best)
        f0s.append(SR / lag)
    return median(f0s) if len(f0s) >= 5 else None


def gender_mismatch(expected: str, f0: float | None) -> str | None:
    """A problem description when the pitch clearly contradicts the expected gender, else None."""
    if f0 is None or expected not in ("male", "female"):
        return None
    if expected == "male" and f0 > MALE_MAX_HZ:
        return f"a male speaker is read by a high voice (median pitch {f0:.0f} Hz)"
    if expected == "female" and f0 < FEMALE_MIN_HZ:
        return f"a female speaker is read by a low voice (median pitch {f0:.0f} Hz)"
    return None
