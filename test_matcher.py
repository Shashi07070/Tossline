"""
test_matcher.py — Unit tests for the strict toss-alert matching engine.

Run with:  python -m pytest test_matcher.py -v
       or:  python test_matcher.py
"""

import unittest

from matcher import TossMatcher


POSITIVE_CASES = [
    "🇷🇼 RWANDA - U19 🇷🇼 WON THE TOSS AND DECIDED TO BAT ✔️✔️",
    "🇺🇬 UGANDA - U19 🇺🇬 WON THE TOSS AND DECIDED TO BAT ✔️✔️",
    "🇳🇬 NIGERIA - U19 🇳🇬 WON THE TOSS AND DECIDED TO BOWL ✔️✔️",
    "🇿🇼 TAKASHINGA - 1 🇿🇼 WON THE TOSS AND DECIDED TO BAT ✔️✔️",
    "🇸🇷 VIKING STARS 🇸🇷 WON THE TOSS AND DECIDED TO BAT ✔️✔️",
    "🇨🇩 OUTER DELHI WARRIOR 🇨🇩 WON THE TOSS AND DECIDED TO BOWL ✔️✔️",
    "🇱🇮 FC GERMANIA 🇱🇮 WON THE TOSS AND DICIDED TO BAT ✔️✔️",
    "🇺🇿 RIMUKA CC 🇺🇿 WON THE TOSS AND DECIDED TO BAT ✔️✔️",
    "🇬🇵 JORHAT STALLION 🇬🇵 WON THE TOSS AND DECIDED TO BOWL ✔️✔️",
    "🏴󠁧󠁢󠁳󠁣󠁴󠁿 SCOTLAND - U15 🏴󠁧󠁢󠁳󠁣󠁴󠁿 WON THE TOSS AND DECIDED TO BOWL ✔️✔️",
    "🇹🇼 TALLIN HIPPOS 🇹🇼 WON THE TOSS AND DECIDED TO BAT ✔️✔️",
    "🇪🇷 SPEEN GHAR WARRIOR 🇪🇷 WON THE TOSS AND DECIDED TO BAT ✔️✔️",
    "🏴󠁧󠁢󠁳󠁣󠁴󠁿 SCOTLAND 🏴󠁧󠁢󠁳󠁣󠁴󠁿 WON THE TOSS AND DECIDED TO BAT ✔️✔️",
    "💙 PAKISTAN BLUES 💙 WON THE TOSS AND DECIDED TO BAT ✔️✔️",
    # Emoji-free variant must still match (emoji are optional decoration).
    "RWANDA - U19 WON THE TOSS AND DECIDED TO BAT ✔️✔️",
]

NEGATIVE_CASES = [
    "WON THE TOSS",
    "Rwanda won the toss.",
    "Rwanda won the toss yesterday.",
    "Rwanda vs Uganda",
    "Rwanda match update",
    "Rwanda won the match ✔️✔️",
    "Rwanda WON THE TOSS but match cancelled.",
    "🔥 Join our channel 🔥",
    "Today's prediction is Rwanda to win.",
    "WON THE TOSS AND DECIDED TO BAT",
    # Additional false-positive guards
    "Good morning",
    "Match starting soon",
    "Score update",
    "Result",
    "Prediction",
    "Follow our channel",
    "Join now",
    "How to win the toss?",
    "Today's match: Rwanda won the toss.",
    "🔥 MATCH UPDATE 🔥 Rwanda won the toss...",
    "RWANDA WON THE TOSS AND DECIDED TO PLAY ✔️✔️",  # invalid decision verb
    "RWANDA WON THE TOSS AND DECIDED TO BAT",         # missing ending checkmarks
    "WON THE TOSS AND DECIDED TO BAT ✔️✔️",           # no team identifier
    "RWANDA WON THE TOSS DECIDED TO BAT ✔️✔️",        # missing connector "AND"
    "RWANDA WON THE TOSS AND DECIDED TO BAT ✔️✔️ Follow our channel for more!",  # trailing junk
]


class TestTossMatcherPositive(unittest.TestCase):
    def test_all_known_good_examples_match(self):
        matcher = TossMatcher()
        for text in POSITIVE_CASES:
            with self.subTest(text=text):
                result = matcher.evaluate(text)
                self.assertTrue(result.matched, f"Expected MATCH, got NO MATCH ({result.reason}) for: {text!r}")
                self.assertIn(result.decision, ("BAT", "BOWL"))
                self.assertTrue(result.team and len(result.team) >= 2)


class TestTossMatcherNegative(unittest.TestCase):
    def test_all_known_bad_examples_do_not_match(self):
        matcher = TossMatcher()
        for text in NEGATIVE_CASES:
            with self.subTest(text=text):
                result = matcher.evaluate(text)
                self.assertFalse(result.matched, f"Expected NO MATCH, got MATCH for: {text!r}")


class TestTossMatcherRobustness(unittest.TestCase):
    def setUp(self):
        self.matcher = TossMatcher()

    def test_empty_and_none_input(self):
        self.assertFalse(self.matcher.is_match(None))
        self.assertFalse(self.matcher.is_match(""))
        self.assertFalse(self.matcher.is_match("   "))

    def test_non_string_input_does_not_raise(self):
        # Fail-closed: unexpected types must not crash the pipeline.
        self.assertFalse(self.matcher.is_match(12345))  # type: ignore[arg-type]
        self.assertFalse(self.matcher.is_match([]))       # type: ignore[arg-type]

    def test_case_insensitivity(self):
        text = "rwanda won the toss and decided to bat ✔️✔️"
        self.assertTrue(self.matcher.is_match(text))

    def test_extra_whitespace_tolerated(self):
        text = "RWANDA   WON THE TOSS   AND   DECIDED TO BAT   ✔️  ✔️"
        self.assertTrue(self.matcher.is_match(text))

    def test_typo_variant_bowl_also_supported(self):
        text = "RWANDA WON THE TOSS AND DICIDED TO BOWL ✔️✔️"
        self.assertTrue(self.matcher.is_match(text))

    def test_only_one_checkmark_fails(self):
        text = "RWANDA WON THE TOSS AND DECIDED TO BAT ✔️"
        self.assertFalse(self.matcher.is_match(text))

    def test_custom_pattern_config_can_be_swapped(self):
        custom = {
            "toss_phrase": "HAS WON THE TOSS",
            "decision_variants": {"CHOSE TO BAT": "BAT", "CHOSE TO BOWL": "BOWL"},
        }
        custom_matcher = TossMatcher(custom)
        self.assertTrue(
            custom_matcher.is_match("INDIA HAS WON THE TOSS AND CHOSE TO BAT ✔️✔️")
        )
        # Old pattern should no longer match under the new config.
        self.assertFalse(
            custom_matcher.is_match("INDIA WON THE TOSS AND DECIDED TO BAT ✔️✔️")
        )

    def test_matcher_never_raises_on_weird_unicode(self):
        weird = "\u200b\u200c RWANDA WON THE TOSS AND DECIDED TO BAT ✔️✔️ \ufeff"
        # Should not raise; result may or may not match depending on normalization,
        # but must not throw.
        try:
            self.matcher.evaluate(weird)
        except Exception as exc:  # pragma: no cover
            self.fail(f"Matcher raised an exception on weird unicode input: {exc}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
