"""PCM helpers. Working format everywhere: 24 kHz, mono, signed 16-bit little-endian.

Silence is always produced here as explicit zero samples — never requested from a speech model."""

from __future__ import annotations

import array
import io
import json
import math
import subprocess
import wave
from dataclasses import dataclass, field

RATE = 24000
BYTES_PER_MS = RATE * 2 // 1000  # 48


class AudioError(Exception):
    pass


def silence(ms: int) -> bytes:
    return b"\x00" * (int(ms) * BYTES_PER_MS)


def duration_ms(pcm: bytes) -> int:
    return len(pcm) // BYTES_PER_MS


def wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def _run(cmd: list[str], data: bytes | None = None) -> bytes:
    try:
        p = subprocess.run(cmd, input=data, capture_output=True, timeout=300, check=False)
    except FileNotFoundError as e:
        raise AudioError(f"{cmd[0]} not found (install FFmpeg)") from e
    if p.returncode != 0:
        raise AudioError(f"{cmd[0]} failed: {p.stderr.decode('utf-8', 'replace')[-400:]}")
    return p.stdout


def decode_to_pcm(data: bytes) -> bytes:
    """Decode any audio container/codec FFmpeg understands to the working PCM format."""
    if not data:
        raise AudioError("empty audio data")
    if data[:4] == b"RIFF":  # our own working-format WAV: no need to spawn FFmpeg
        try:
            with wave.open(io.BytesIO(data), "rb") as w:
                if (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getcomptype()) == (1, 2, RATE, "NONE"):
                    pcm = w.readframes(w.getnframes())
                    if len(pcm) >= BYTES_PER_MS * 20:
                        return pcm
        except (wave.Error, EOFError):
            pass
    out = _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            str(RATE),
            "pipe:1",
        ],
        data,
    )
    if len(out) < BYTES_PER_MS * 20:
        raise AudioError("decoded audio is empty")
    return out


def encode_mp3(pcm: bytes, bitrate: str = "64k") -> bytes:
    return _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "s16le",
            "-ar",
            str(RATE),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            bitrate,
            "-ar",
            str(RATE),
            "-write_xing",
            "1",
            "-f",
            "mp3",
            "pipe:1",
        ],
        pcm,
    )


def probe(data: bytes) -> dict:
    out = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name:stream=codec_name,sample_rate,channels",
            "-of",
            "json",
            "-i",
            "pipe:0",
        ],
        data,
    )
    info = json.loads(out or b"{}")
    streams = info.get("streams") or []
    if not streams:
        raise AudioError("no audio stream")
    return {
        "codec": streams[0].get("codec_name"),
        "format": (info.get("format") or {}).get("format_name"),
        "duration": float((info.get("format") or {}).get("duration") or 0),
    }


def rms_dbfs(pcm: bytes, step: int = 4) -> float:
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    if not a:
        return -120.0
    sample = a[::step]
    ms = sum(x * x for x in sample) / len(sample)
    return -120.0 if ms <= 0 else 20 * math.log10(math.sqrt(ms) / 32768.0)


def peak_ratio(pcm: bytes, step: int = 4) -> float:
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    return max((abs(x) for x in a[::step]), default=0) / 32768.0


@dataclass
class Track:
    """Sequential PCM assembly with a timeline. The same clip object can be appended many times
    (repeat plays, evidence replay) so repeated audio is byte-identical."""

    parts: list[bytes] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)
    pos_ms: int = 0

    def add(self, kind: str, pcm: bytes, **meta) -> None:
        d = duration_ms(pcm)
        self.timeline.append({"kind": kind, "start_ms": self.pos_ms, "end_ms": self.pos_ms + d, **meta})
        self.parts.append(pcm)
        self.pos_ms += d

    def gap(self, ms: int, kind: str = "gap", **meta) -> None:
        if ms > 0:
            self.add(kind, silence(ms), **meta)

    def pcm(self) -> bytes:
        return b"".join(self.parts)
