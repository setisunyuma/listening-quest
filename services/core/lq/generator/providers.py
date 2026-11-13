"""Provider interfaces. Content generation, blind review, speech synthesis and speech recognition are
separate so each can be swapped (demo / OpenAI) and budgeted independently."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class ProviderError(Exception):
    """retryable=False for errors that will not go away by retrying (bad request, schema refusal)."""

    def __init__(self, msg: str, retryable: bool = True):
        super().__init__(msg)
        self.retryable = retryable


@dataclass(frozen=True)
class GenSpec:
    mode: str
    part: str
    template_type: str
    primary_skill: str
    band: int
    topic_hint: str = ""
    seed: str = "0"
    source: str = "generated"
    pool: str = "practice"
    avoid_topics: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)


class ContentProvider(Protocol):
    name: str
    model: str

    def generate(self, spec: GenSpec, meter) -> dict: ...  # returns UnitContent-compatible dict


class ReviewProvider(Protocol):
    name: str
    model: str
    record_method: str  # api_reviewed | demo_mock_reviewed

    def solve(self, payload: dict, meter) -> dict: ...  # {"answers": {qid: answer|None}, "ambiguous": [qid], "notes": str}


class TTSProvider(Protocol):
    name: str
    model: str
    audio_kind: str  # tts | demo_tone

    def synthesize(self, text: str, voice: str, lang: str, instructions: str, meter) -> bytes: ...  # PCM 24k mono s16le


class ASRProvider(Protocol):
    name: str
    model: str
    record_method: str  # audio_auto_checked | audio_demo_mock_checked

    def transcribe(self, pcm: bytes, lang: str, meter, expected_hint: str = "") -> str: ...
