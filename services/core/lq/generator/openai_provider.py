"""OpenAI adapters (real API). Verified against the public docs on 2026-11-12:

- Structured output: POST /v1/responses with text.format = {type: json_schema, strict: true}.
- Speech: tts-1 / tts-1-hd / gpt-4o-mini-tts on /v1/audio/speech are deprecated (shutdown 2027-01-06);
  the documented replacement is gpt-realtime-2.1-mini through the Realtime API, which is NOT accepted by
  /v1/audio/speech. `RealtimeTTS` therefore opens a server-side WebSocket session per utterance, collects
  `response.output_audio.delta` chunks and waits for `response.done` (which carries no audio itself).
  The voice is set before any audio is produced and never changed inside a session.
- Transcription: POST /v1/audio/transcriptions (default gpt-transcribe).
The transcript a speech model reports for its own output is never used as an independent ASR result.
"""

from __future__ import annotations

import base64
import json

import httpx

from lq.audio.pcm import decode_to_pcm, duration_ms, wav_bytes
from lq.generator import budget
from lq.generator.providers import GenSpec, ProviderError

TIMEOUT_NOTE = "request timed out or connection dropped; billing status unknown"


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _post_json(url: str, key: str, body: dict, timeout: float, meter, entry: str) -> dict:
    try:
        r = httpx.post(url, headers=_headers(key), json=body, timeout=timeout)
    except httpx.ConnectError as e:  # never reached the server
        meter.release(entry)
        raise ProviderError(f"connection failed: {e}") from e
    except httpx.HTTPError as e:
        meter.mark_unknown(entry, TIMEOUT_NOTE)
        raise ProviderError(f"{TIMEOUT_NOTE}: {e}") from e
    if r.status_code >= 400:
        # 4xx responses are not billed for generation; 5xx is uncertain
        if r.status_code < 500:
            meter.release(entry)
        else:
            meter.mark_unknown(entry, f"http {r.status_code}")
        raise ProviderError(f"OpenAI HTTP {r.status_code}: {r.text[:300]}", retryable=r.status_code in (408, 409, 429) or r.status_code >= 500)
    return r.json()


def _output_text(resp: dict) -> str:
    if resp.get("status") not in (None, "completed"):
        raise ProviderError(f"response status {resp.get('status')}: {resp.get('incomplete_details')}")
    chunks = []
    for item in resp.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    chunks.append(part.get("text", ""))
                elif part.get("type") == "refusal":
                    raise ProviderError(f"model refused: {part.get('refusal')}", retryable=False)
    if not chunks:
        raise ProviderError("no output_text in response")
    return "".join(chunks)


class _Structured:
    name = "openai"

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float):
        self.key, self.base, self.model, self.timeout = api_key, base_url.rstrip("/"), model, timeout

    def _call(self, step: str, system: str, user: str, schema_name: str, schema: dict, max_output_tokens: int, meter) -> dict:
        est = budget.est_text(self.model, len(system) + len(user) + len(json.dumps(schema)), max_output_tokens)
        entry = meter.reserve(step, self.name, self.model, est)
        body = {
            "model": self.model,
            "input": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
            "max_output_tokens": max_output_tokens,
            "store": False,
        }
        resp = _post_json(f"{self.base}/responses", self.key, body, self.timeout, meter, entry)
        usage = resp.get("usage") or {}
        meter.settle(entry, budget.cost_text(self.model, usage), usage)
        try:
            return json.loads(_output_text(resp))
        except json.JSONDecodeError as e:
            raise ProviderError(f"structured output was not valid JSON: {e}") from e


class OpenAIContent(_Structured):
    def generate(self, spec: GenSpec, meter) -> dict:
        from lq.generator import authoring

        system, user = authoring.build_generation_prompt(spec)
        raw = self._call("content", system, user, "listening_unit", authoring.authored_schema(), 6000, meter)
        return authoring.authored_to_unit(raw, spec)


class OpenAIReview(_Structured):
    record_method = "api_reviewed"

    def solve(self, payload: dict, meter) -> dict:
        from lq.generator import authoring

        system, user = authoring.build_review_prompt(payload)
        raw = self._call("review", system, user, "blind_solution", authoring.SOLUTION_SCHEMA, 2500, meter)
        return authoring.solution_to_answers(raw, payload)


TTS_INSTRUCTIONS = (
    "You are a voice actor recording exam listening material. Read the user's text aloud exactly as written, "
    "word for word, in {lang}. Do not add, omit, translate, answer or comment on anything. Natural pace. {extra}"
)


class RealtimeTTS:
    name = "openai_realtime"
    audio_kind = "tts"

    def __init__(self, api_key: str, url: str, model: str, timeout: float):
        self.key, self.url, self.model, self.timeout = api_key, url, model, timeout

    def synthesize(self, text: str, voice: str, lang: str, instructions: str, meter) -> bytes:
        from websockets.exceptions import WebSocketException
        from websockets.sync.client import connect

        entry = meter.reserve("tts", self.name, self.model, budget.est_tts("realtime", self.model, text))
        lang_name = {"en": "English", "ja": "Japanese"}.get(lang, lang)
        chunks: list[bytes] = []
        usage: dict = {}
        sent = False
        try:
            with connect(f"{self.url}?model={self.model}", additional_headers=_headers(self.key), open_timeout=self.timeout, max_size=None) as ws:
                ws.send(json.dumps({
                    "type": "session.update",
                    "session": {
                        "type": "realtime",
                        "output_modalities": ["audio"],
                        "instructions": TTS_INSTRUCTIONS.format(lang=lang_name, extra=instructions),
                        "audio": {"input": {"turn_detection": None}, "output": {"format": {"type": "audio/pcm", "rate": 24000}, "voice": voice}},
                    },
                }))  # fmt: skip
                ws.send(json.dumps({"type": "conversation.item.create", "item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}))
                ws.send(json.dumps({"type": "response.create", "response": {"output_modalities": ["audio"]}}))
                sent = True
                while True:
                    ev = json.loads(ws.recv(timeout=self.timeout))
                    t = ev.get("type")
                    if t == "response.output_audio.delta":
                        chunks.append(base64.b64decode(ev["delta"]))
                    elif t == "error":
                        raise ProviderError(f"realtime error: {ev.get('error')}")
                    elif t == "response.done":
                        resp = ev.get("response") or {}
                        usage = resp.get("usage") or {}
                        if resp.get("status") != "completed":
                            raise ProviderError(f"realtime response {resp.get('status')}: {resp.get('status_details')}")
                        break
        except ProviderError:
            meter.mark_unknown(entry, "realtime error after request")
            raise
        except (WebSocketException, TimeoutError, OSError) as e:
            if sent:
                meter.mark_unknown(entry, TIMEOUT_NOTE)
            else:
                meter.release(entry)
            raise ProviderError(f"realtime connection problem: {e}") from e
        meter.settle(entry, budget.cost_realtime(self.model, usage), usage)
        pcm = b"".join(chunks)
        if duration_ms(pcm) < 100:
            raise ProviderError("realtime returned no audio")
        return pcm


class SpeechTTS:
    """Legacy /v1/audio/speech. Kept selectable until the announced shutdown (2027-01-06)."""

    name = "openai_speech"
    audio_kind = "tts"

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float):
        self.key, self.base, self.model, self.timeout = api_key, base_url.rstrip("/"), model, timeout

    def synthesize(self, text: str, voice: str, lang: str, instructions: str, meter) -> bytes:
        entry = meter.reserve("tts", self.name, self.model, budget.est_tts("speech", self.model, text))
        body = {"model": self.model, "voice": voice, "input": text, "response_format": "wav", "instructions": instructions or "Read clearly at a natural pace."}
        try:
            r = httpx.post(f"{self.base}/audio/speech", headers=_headers(self.key), json=body, timeout=self.timeout)
        except httpx.ConnectError as e:
            meter.release(entry)
            raise ProviderError(str(e)) from e
        except httpx.HTTPError as e:
            meter.mark_unknown(entry, TIMEOUT_NOTE)
            raise ProviderError(f"{TIMEOUT_NOTE}: {e}") from e
        if r.status_code >= 400:
            (meter.release if r.status_code < 500 else lambda x: meter.mark_unknown(x, f"http {r.status_code}"))(entry)
            raise ProviderError(f"OpenAI HTTP {r.status_code}: {r.text[:300]}", retryable=r.status_code in (408, 429) or r.status_code >= 500)
        meter.settle(entry, None, {"note": "speech endpoint returns no usage; cost stays at the estimate"})
        return decode_to_pcm(r.content)


class OpenAIASR:
    name = "openai"
    record_method = "audio_auto_checked"

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float):
        self.key, self.base, self.model, self.timeout = api_key, base_url.rstrip("/"), model, timeout

    def transcribe(self, pcm: bytes, lang: str, meter, expected_hint: str = "") -> str:
        # expected_hint is deliberately NOT sent: the recogniser must not be primed with the script.
        seconds = duration_ms(pcm) / 1000
        est = budget.est_asr(self.model, seconds)
        entry = meter.reserve("asr", self.name, self.model, est)
        try:
            r = httpx.post(
                f"{self.base}/audio/transcriptions",
                headers=_headers(self.key),
                data={"model": self.model, "language": lang, "response_format": "json"},
                files={"file": ("clip.wav", wav_bytes(pcm), "audio/wav")},
                timeout=self.timeout,
            )
        except httpx.ConnectError as e:
            meter.release(entry)
            raise ProviderError(str(e)) from e
        except httpx.HTTPError as e:
            meter.mark_unknown(entry, TIMEOUT_NOTE)
            raise ProviderError(f"{TIMEOUT_NOTE}: {e}") from e
        if r.status_code >= 400:
            (meter.release if r.status_code < 500 else lambda x: meter.mark_unknown(x, f"http {r.status_code}"))(entry)
            raise ProviderError(f"OpenAI HTTP {r.status_code}: {r.text[:300]}", retryable=r.status_code in (408, 429) or r.status_code >= 500)
        meter.settle(entry, est, {"seconds": seconds, "basis": "per-minute price x measured duration"})
        return r.json().get("text", "")
