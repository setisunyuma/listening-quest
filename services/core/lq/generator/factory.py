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


_MALE_WORDS = {"man", "male", "boy", "mr", "mr.", "father", "dad", "son", "brother", "husband", "grandfather", "he"}
_FEMALE_WORDS = {
    "woman",
    "female",
    "girl",
    "ms",
    "ms.",
    "mrs",
    "mrs.",
    "miss",
    "mother",
    "mom",
    "daughter",
    "sister",
    "wife",
    "grandmother",
    "she",
}


def speaker_gender(sp) -> str:
    """'male' | 'female' | 'neutral'. A gendered label ("Man", "Woman", "Mr. Lee") wins, because the learner
    reads the label while hearing the voice; otherwise the authored voice_hint decides."""
    words = {w.strip(".,:;()").lower() for w in str(getattr(sp, "label", "")).split()}
    if words & _FEMALE_WORDS and not words & _MALE_WORDS:
        return "female"
    if words & _MALE_WORDS and not words & _FEMALE_WORDS:
        return "male"
    hint = getattr(sp, "voice_hint", "neutral") or "neutral"
    return hint if hint in ("male", "female") else "neutral"


def _pool(csv: str, exclude: set[str]) -> list[str]:
    return [v.strip() for v in csv.split(",") if v.strip() and v.strip() not in exclude]


def assign_voices(unit) -> dict[str, str]:
    """One fixed voice per speaker_id inside a unit. A speaker's voice matches its gender (voice_hint, else
    the label), different speakers never share a voice while the pools last, and the narrator and the
    Japanese explainer keep their own voices."""
    s = get_settings()
    reserved = {s.tts_narrator_voice, s.tts_ja_voice}
    pools = {
        "male": _pool(s.tts_male_voices, reserved),
        "female": _pool(s.tts_female_voices, reserved),
        "neutral": _pool(s.tts_speaker_voices, reserved),
    }
    used: set[str] = set()
    voices = {"explainer_ja": s.tts_ja_voice}
    for sp in unit.speakers:
        if sp.role == "narrator":
            voices[sp.id] = s.tts_narrator_voice
            continue
        pool = pools[speaker_gender(sp)] or pools["neutral"]
        fresh = [v for v in pool if v not in used]
        voice = fresh[0] if fresh else pool[len(used) % len(pool)]
        voices[sp.id] = voice
        used.add(voice)
    voices.setdefault("narrator", s.tts_narrator_voice)
    return voices


def voice_config_problems(unit, voices: dict[str, str]) -> list[str]:
    """R10: deterministic check of the voice settings before any speech is made (free). Each speaker's voice must
    come from the pool of its gender, a voice must not sit in both gender pools, and speakers must not share a
    voice while the pools allow distinct ones (or with the narrator)."""
    s = get_settings()
    reserved = {s.tts_narrator_voice, s.tts_ja_voice}
    male, female = set(_pool(s.tts_male_voices, reserved)), set(_pool(s.tts_female_voices, reserved))
    errs = []
    both = male & female
    if both:
        errs.append(f"voice settings: {sorted(both)} are listed as both male and female voices")
    speakers = [sp for sp in unit.speakers if sp.role != "narrator"]
    for sp in speakers:
        v, g = voices.get(sp.id), speaker_gender(sp)
        if v is None:
            errs.append(f"speaker {sp.id}: no voice assigned")
        elif g == "male" and male and v in female - male:
            errs.append(f"speaker {sp.id} is male but is assigned the female voice {v}")
        elif g == "female" and female and v in male - female:
            errs.append(f"speaker {sp.id} is female but is assigned the male voice {v}")
        if v is not None and v == s.tts_narrator_voice:
            errs.append(f"speaker {sp.id} shares the narrator's voice")
    used = [voices.get(sp.id) for sp in speakers]
    if len(set(used)) < len(used) and len(set(male | female | set(_pool(s.tts_speaker_voices, reserved)))) >= len(used):
        errs.append("two speakers share one voice although distinct voices are available")
    return errs
