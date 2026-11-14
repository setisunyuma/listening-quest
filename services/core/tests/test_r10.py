"""R10: the pitch screen is an anomaly check against the specified gender, not a voice-quality guarantee."""

from helpers import make_content

from lq.audio import pitch
from lq.config import get_settings
from lq.generator.factory import assign_voices, voice_config_problems


def test_pitch_states_are_kept_apart_and_unmeasurable_is_not_a_pass():
    assert pitch.pitch_check("male", None)["state"] == "unmeasurable"
    assert "合格扱いにしません" in pitch.PITCH_STATE_JA["unmeasurable"]
    assert pitch.pitch_check("male", 120)["state"] == "ok"
    assert pitch.pitch_check("male", 170)["state"] == "borderline"
    assert pitch.pitch_check("male", 230)["state"] == "mismatch"
    assert pitch.pitch_check("female", 155)["state"] == "borderline"
    assert pitch.pitch_check("female", 110)["state"] == "mismatch"
    assert pitch.pitch_check("neutral", 110)["state"] == "not_applicable"
    assert "品質保証ではありません" in pitch.PITCH_STATE_JA["ok"]


def test_voice_settings_mixups_are_found_before_any_speech(monkeypatch):
    u = make_content(part="3", seed="voices")  # two speakers, male and female
    voices = assign_voices(u)
    assert voice_config_problems(u, voices) == []
    sp = [s for s in u.speakers if s.role != "narrator"]
    swapped = {**voices, sp[0].id: voices[sp[1].id], sp[1].id: voices[sp[0].id]}
    errs = voice_config_problems(u, swapped)
    assert any("assigned the" in e for e in errs)
    s = get_settings()
    monkeypatch.setattr(s, "tts_female_voices", s.tts_female_voices + "," + s.tts_male_voices.split(",")[0])
    assert any("both male and female" in e for e in voice_config_problems(u, assign_voices(u)))
