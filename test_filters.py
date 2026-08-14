"""
test_filters.py — Unit tests for link/blacklist filtering (replaces test_matcher.py).

Run with: python -m pytest test_filters.py -v
      or: python test_filters.py
"""

import unittest
from types import SimpleNamespace

from filters import contains_link, contains_blacklisted_term, is_text_only_message, evaluate_message


class TestLinkDetection(unittest.TestCase):
    def test_detects_common_link_forms(self):
        cases = [
            "check this out https://example.com",
            "visit http://example.com/page",
            "www.example.com",
            "join t.me/somechannel",
            "telegram.me/somechannel",
            "telegram.dog/somechannel",
            "promo.win now",
            "casino.bet",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertTrue(contains_link(text))

    def test_case_insensitive(self):
        self.assertTrue(contains_link("HTTPS://EXAMPLE.COM"))
        self.assertTrue(contains_link("WWW.EXAMPLE.COM"))

    def test_plain_text_no_link(self):
        cases = [
            "Good morning everyone",
            "RWANDA vs UGANDA today",
            "Match starting soon",
            "This is a normal update.",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertFalse(contains_link(text))

    def test_empty_input(self):
        self.assertFalse(contains_link(""))
        self.assertFalse(contains_link(None))


class TestBlacklist(unittest.TestCase):
    def test_matches_case_insensitive_substring(self):
        blacklist = ["casino", "free bet"]
        self.assertEqual(contains_blacklisted_term("Visit our CASINO now", blacklist), "casino")
        self.assertEqual(contains_blacklisted_term("Get your Free Bet today", blacklist), "free bet")

    def test_no_match(self):
        blacklist = ["casino", "free bet"]
        self.assertIsNone(contains_blacklisted_term("Rwanda won the toss", blacklist))

    def test_empty_blacklist(self):
        self.assertIsNone(contains_blacklisted_term("anything", []))

    def test_empty_text(self):
        self.assertIsNone(contains_blacklisted_term("", ["casino"]))


def _fake_message(text=None, media=None, **extra_attrs):
    base = {
        "raw_text": text,
        "message": text,
        "media": media,
        "photo": None, "video": None, "gif": None, "sticker": None,
        "voice": None, "video_note": None, "audio": None, "document": None,
        "contact": None, "geo": None, "venue": None, "poll": None, "game": None,
    }
    base.update(extra_attrs)
    return SimpleNamespace(**base)


class TestMediaGate(unittest.TestCase):
    def test_plain_text_is_text_only(self):
        msg = _fake_message(text="Hello world")
        self.assertTrue(is_text_only_message(msg))

    def test_photo_with_caption_is_not_text_only(self):
        msg = _fake_message(text="nice caption", media=object(), photo=object())
        self.assertFalse(is_text_only_message(msg))

    def test_sticker_is_not_text_only(self):
        msg = _fake_message(text=None, media=object(), sticker=object())
        self.assertFalse(is_text_only_message(msg))

    def test_empty_text_is_not_text_only(self):
        msg = _fake_message(text="")
        self.assertFalse(is_text_only_message(msg))

    def test_video_is_not_text_only(self):
        msg = _fake_message(text=None, media=object(), video=object())
        self.assertFalse(is_text_only_message(msg))

    def test_gif_is_not_text_only(self):
        msg = _fake_message(text=None, media=object(), gif=object())
        self.assertFalse(is_text_only_message(msg))


class TestEvaluateMessage(unittest.TestCase):
    def test_plain_text_allowed(self):
        msg = _fake_message(text="Rwanda won the toss and decided to bat")
        result = evaluate_message(msg, blacklist=[])
        self.assertTrue(result.allowed)

    def test_text_with_link_blocked(self):
        msg = _fake_message(text="check this https://example.com")
        result = evaluate_message(msg, blacklist=[])
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "contains_link")

    def test_text_with_blacklisted_word_blocked(self):
        msg = _fake_message(text="best casino offer")
        result = evaluate_message(msg, blacklist=["casino"])
        self.assertFalse(result.allowed)
        self.assertTrue(result.reason.startswith("blacklisted_term:"))

    def test_photo_blocked_even_with_clean_caption(self):
        msg = _fake_message(text="totally clean caption", media=object(), photo=object())
        result = evaluate_message(msg, blacklist=[])
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "non_text_or_media_message")

    def test_sticker_blocked(self):
        msg = _fake_message(text=None, media=object(), sticker=object())
        result = evaluate_message(msg, blacklist=[])
        self.assertFalse(result.allowed)

    def test_video_blocked(self):
        msg = _fake_message(text=None, media=object(), video=object())
        result = evaluate_message(msg, blacklist=[])
        self.assertFalse(result.allowed)

    def test_never_raises_on_broken_input(self):
        class Weird:
            def __getattr__(self, item):
                raise RuntimeError("boom")
        try:
            result = evaluate_message(Weird(), blacklist=[])
            self.assertFalse(result.allowed)
        except Exception as exc:  # pragma: no cover
            self.fail(f"evaluate_message raised instead of failing closed: {exc}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
