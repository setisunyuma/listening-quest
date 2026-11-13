"""Choose providers per realm. demo realm -> demo providers (free). live realm -> OpenAI adapters when an
API key exists; whether they may actually spend money is decided by BudgetMeter (paid_enabled + budget)."""

from __future__ import annotations

from dataclasses import dataclass

from lq.config import get_settings
from lq.generator.budget import BudgetMeter, FreeMeter


@dataclass
class Providers:
    content: object | None
    review: object | None
    tts: object | None
    asr: object | None
    paid: bool

    def meter(self, realm: str, job_id: str | None):
        return BudgetMeter(realm, job_id) if self.paid else FreeMeter()


_override: dict[str, Providers] = {}


def set_provider_override(realm: str, providers: Providers | None) -> None:
    if providers is None:
        _override.pop(realm, None)
    else:
        _override[realm] = providers


def get_providers(realm: str) -> Providers:
    if realm in _override:
        return _override[realm]
    if realm == "demo":
        from lq.generator.demo import DemoASR, DemoContent, DemoReview, DemoTTS

        return Providers(DemoContent(), DemoReview(), DemoTTS(), DemoASR(), paid=False)
    s = get_settings()
    if not s.openai_api_key:
        return Providers(None, None, None, None, paid=True)
    from lq.generator.openai_provider import OpenAIASR, OpenAIContent, OpenAIReview, RealtimeTTS, SpeechTTS

    tts = (
        SpeechTTS(s.openai_api_key, s.openai_base_url, s.tts_speech_model, s.provider_timeout_sec)
        if s.tts_provider == "openai_speech"
        else RealtimeTTS(s.openai_api_key, s.openai_realtime_url, s.tts_model, s.provider_timeout_sec)
    )
    return Providers(
        OpenAIContent(s.openai_api_key, s.openai_base_url, s.content_model, s.provider_timeout_sec),
        OpenAIReview(s.openai_api_key, s.openai_base_url, s.review_model, s.provider_timeout_sec),
        tts,
        OpenAIASR(s.openai_api_key, s.openai_base_url, s.asr_model, s.provider_timeout_sec),
        paid=True,
    )


def assign_voices(unit) -> dict[str, str]:
    """One fixed voice per speaker_id inside a unit; narrator and the Japanese explainer are distinct."""
    s = get_settings()
    pool = [
        v.strip() for v in s.tts_speaker_voices.split(",") if v.strip() and v.strip() not in (s.tts_narrator_voice,)
    ]
    voices = {"explainer_ja": s.tts_ja_voice}
    i = 0
    for sp in unit.speakers:
        if sp.role == "narrator":
            voices[sp.id] = s.tts_narrator_voice
        else:
            voices[sp.id] = pool[i % len(pool)]
            i += 1
    voices.setdefault("narrator", s.tts_narrator_voice)
    return voices
