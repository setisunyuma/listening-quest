"""R01 / R02: dictation and the audio check compare key words in position (instruction document 2026-11-14)."""

import pytest

from lq.audio.align import compare
from lq.audio.qa import compare_transcript
from lq.audio.textnorm import normalize_tokens

# ---- R02: audio check (expected script vs speech recognition)


@pytest.mark.parametrize(
    "script,heard",
    [
        ("Anna paid 20 dollars and Ben paid 30 dollars.", "Anna paid 30 dollars and Ben paid 20 dollars."),  # swap
        ("The temperature was -5 degrees.", "The temperature was 5 degrees."),  # sign lost
        ("The meeting about the new budget for the sports club will be held on Monday afternoon in room three.",
         "The meeting about the new budget for the sports club will be held on Tuesday afternoon in room three."),
        ("The train leaves at nine in the morning from the new station.", "The train arrives at nine in the morning from the new station."),
        ("Turn left at the bank.", "Turn right at the bank."),
        ("I didn't buy the blue one.", "I did buy the blue one."),  # negation lost
        ("Take the bus, not the train.", "Take the bus, the train."),
        ("Anna called Ben yesterday.", "Ben called Anna yesterday."),  # people swapped
        ("It is cheaper than the old one.", "It is bigger than the old one."),
    ],
)  # fmt: skip
def test_meaning_changes_fail_even_with_a_low_error_rate(script, heard):
    r = compare_transcript(script, heard, is_evidence=True)
    assert not r["ok"], r
    assert any(p["type"] == "key_word_mismatch" for p in r["problems"])


@pytest.mark.parametrize(
    "script,heard",
    [
        ("The temperature was -5 degrees.", "The temperature was minus five degrees."),
        ("The temperature was −5 degrees.", "the temperature was negative five degrees"),
        ("It costs 100 dollars.", "It costs one hundred dollars."),
        ("Sales rose by 50%.", "Sales rose by fifty percent."),
        ("I can't come.", "I cannot come."),
        ("We met in 2019.", "We met in twenty nineteen."),
        ("The train leaves at 9:30.", "The train leaves at nine thirty."),
        (
            "Yesterday Anna paid twenty dollars for the concert tickets.",
            "Yesterday Ana paid twenty dollars for the concert tickets.",
        ),  # a misheard name alone is not a meaning change
        ("A well-known writer spoke.", "A well known writer spoke."),  # a hyphen between words is not a sign
    ],
)
def test_notation_differences_still_pass(script, heard):
    r = compare_transcript(script, heard)
    assert r["ok"], r


def test_signed_numbers_normalise():
    assert normalize_tokens("-5")[-1] == "#-5" and normalize_tokens("minus five")[-1] == "#-5"
    assert normalize_tokens("5")[-1] == "#5" and normalize_tokens("pages 10-20") == ["pages", "#10", "#20"]


# ---- R01: dictation scoring


def _score(expected, typed):
    c = compare(expected, typed)
    ref = c["ref"]
    return (max(0.0, 1 - c["edits"] / len(ref)) if ref else 0.0), c


def test_inserted_words_and_added_negation_cost_points():
    s, c = _score("I like tea.", "I do not like tea.")
    assert s < 1 and any(f["type"] == "key_added" and f["got"] == "not" for f in c["findings"])
    s, _ = _score("I like tea.", "I like tea tea.")
    assert s < 1
    s, c = _score("It was 20 dollars.", "It was 30 dollars.")
    assert s < 1 and c["findings"][0]["type"] == "key_changed"
    s, c = _score("I didn't go.", "I go.")
    assert s < 1 and any(f["type"] == "key_missing" for f in c["findings"])
    s, _ = _score("tea I like", "I like tea")  # word order
    assert s < 1
    s, c = _score("I like tea.", "")
    assert s == 0
    s, c = _score("It costs 100 dollars.", "it costs one hundred dollars")
    assert s == 1 and not c["findings"]
