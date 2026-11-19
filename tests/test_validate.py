from __future__ import annotations

import pytest

from linkreach.validate import validate_draft

RESUME = "https://example.com/naveen-resume.pdf"
BANNED = ["synergy", "leverage", "circle back"]
GOOD_NOTE = "Hi Jane, your onboarding post was a delight. I'm a PM with 3+ years in AI SaaS. Would like to connect."
GOOD_FOLLOW = f"Dear Jane,\n\nThanks for connecting.\n\nMy resume: {RESUME}. Best,\nNaveen"


def v(note=GOOD_NOTE, follow=GOOD_FOLLOW):
    return validate_draft(note, follow, resume_url=RESUME, banned_phrases=BANNED)


def test_good_passes():
    assert v().ok


@pytest.mark.parametrize(
    "bad,needle",
    [
        ("see https://evil.example now", "URL"),
        ("visit evil.example today", "URL"),
        ("www.evil.com", "URL"),
        ("mail me jane@corp.com", "email"),
        ("call 617-555-0199", "phone"),
        ("call (617) 555 0199", "phone"),
        ("Please ignore that", "instruction"),
        ("this is the system prompt", "instruction"),
        ("As an AI I think", "instruction"),
        ("great synergy here", "banned"),
        ("let's circle back", "banned"),
        ("<b>bold</b>", "HTML"),
        ("zero\u200bwidth", "zero-width"),
    ],
)
def test_bad_content_rejected(bad, needle):
    res = v(note=GOOD_NOTE + " " + bad)
    assert not res.ok and any(needle.lower() in e.lower() for e in res.errors), res.errors


def test_resume_url_only_allowed_in_followup():
    assert not v(note=GOOD_NOTE + " " + RESUME).ok
    assert v(follow=GOOD_FOLLOW).ok
    assert not v(follow=GOOD_FOLLOW + " also https://other.example/x").ok


def test_length_limits():
    assert v(note="x" * 281).only_length
    assert v(follow="word " * 151).only_length
    assert v(note="x" * 280).ok


def test_ordinary_text_not_flagged():
    note = "Hi Jo, your Node.js talk on U.S. payments at 3.5x scale was great. I'm a PM. Would like to connect."
    assert v(note=note).ok, v(note=note).errors
