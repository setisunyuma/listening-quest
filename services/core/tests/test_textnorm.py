"""Audio check regression tests: notation differences pass, meaning differences fail."""

import pytest

from lq.audio.qa import compare_transcript
from lq.audio.textnorm import normalize_tokens

SAME = [
    ("It costs one hundred dollars.", "It costs $100."),
    ("It costs a hundred dollars.", "It costs 100 dollars"),
    ("The average was three point five.", "The average was 3.5"),
    ("We expect two point zero five meters.", "we expect 2.05 meters"),
    ("The law changed in twenty twenty-six.", "The law changed in 2026."),
    ("She was born in nineteen ninety-nine.", "She was born in 1999"),
    ("It opened in nineteen oh five.", "It opened in 1905."),
    ("It opened in two thousand and five.", "It opened in 2005."),
    ("About fifteen hundred people came.", "About 1,500 people came."),
    ("One thousand two hundred and fifty tickets were sold.", "1,250 tickets were sold"),
    ("The bus leaves at nine thirty.", "The bus leaves at 9:30."),
    ("The bus leaves at ten oh five.", "The bus leaves at 10:05"),
    ("It starts at nine.", "It starts at 9:00"),
    ("Sales rose by fifty percent.", "Sales rose by 50%."),
    ("Sales rose by fifty per cent.", "Sales rose by 50 percent"),
    ("The meeting is on the twenty-first.", "The meeting is on the 21st."),
    ("Take the second exit.", "Take the 2nd exit"),
    ("Twenty-five students passed.", "25 students passed."),
    ("I can't come on Friday.", "I cannot come on Friday."),
    ("She won't be there.", "she will not be there"),
    ("It's the third time.", "It's the 3rd time"),
    ("Wait a second, please.", "Wait a second please"),
    ("A million people live there.", "1,000,000 people live there"),
]

DIFFERENT = [
    ("It costs one hundred dollars.", "It costs 110 dollars.", "number"),
    ("The average was three point five.", "The average was 3.15", "number"),
    ("The average was three point five.", "The average was three five", "number"),
    ("The law changed in twenty twenty-six.", "The law changed in 2016.", "number"),
    ("The fee is fifteen dollars.", "The fee is fifty dollars.", "number"),
    ("The bus leaves at nine thirty.", "The bus leaves at 9:13.", "number"),
    ("The meeting is on the twenty-first.", "The meeting is on the 20th.", "number"),
    ("About fifteen hundred people came.", "About 1,050 people came.", "number"),
    ("Sales rose by fifty percent.", "Sales rose by 15%.", "number"),
    ("I can't come on Friday.", "I can come on Friday.", "negation"),
    ("It does not start at nine.", "It does start at nine.", "negation"),
    ("There is no fee.", "There is a fee.", "negation"),
    ("We never open on Sunday.", "We open on Sunday.", "negation"),
    ("Bring two pens.", "Bring pens.", "number"),
    ("It closes at five.", "It closes at five or six.", "number"),
]


@pytest.mark.parametrize(("script", "heard"), SAME)
def test_notation_differences_pass(script, heard):
    r = compare_transcript(script, heard, is_evidence=True)
    assert r["ok"], (normalize_tokens(script), normalize_tokens(heard), r)


@pytest.mark.parametrize(("script", "heard", "kind"), DIFFERENT)
def test_meaning_differences_fail(script, heard, kind):
    r = compare_transcript(script, heard)
    assert not r["ok"]
    # qa-2 (R02): the key words are compared in position; the problem type was renamed with that change
    assert any(p["type"] == "key_word_mismatch" for p in r["problems"]), (kind, r)


def test_canonical_tokens():
    assert normalize_tokens("one hundred and twenty-three") == ["#123"]
    assert normalize_tokens("three point one four") == ["#3.14"]
    assert normalize_tokens("$3.50") == ["#3.5", "dollars"]
    assert normalize_tokens("nineteen eighty-four") == ["#1984"]
    assert normalize_tokens("one and two") == ["#1", "and", "#2"]  # "and" between plain numbers is not part of a number
    assert normalize_tokens("twenty-first century") == ["#21st", "century"]
    assert normalize_tokens("Oh, really?") == ["oh", "really"]  # an interjection is not zero
    assert normalize_tokens("I'll wait a second.")[-1] == "second"


def test_long_mismatch_still_fails_on_word_error_rate():
    r = compare_transcript(
        "Please return the books to the front desk by Friday.", "Please remember the cooks at the front gate on Friday."
    )
    assert not r["ok"] and any(p["type"] == "wer_too_high" for p in r["problems"])
