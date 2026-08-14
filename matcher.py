"""
matcher.py — Strict multi-layer toss-alert pattern matching engine.

Design goals (per spec):
  - Conservative: when uncertain, DO NOT MATCH.
  - No hard-coded team names — team identifier is recognized structurally.
  - Emoji/flags are optional decoration, never required.
  - Typo variants (DICIDED) are explicit and configurable, not fuzzy.
  - Fails closed: any exception during matching is treated as NO MATCH.

The engine is configuration-driven so an admin can swap in a different
pattern later via /setpattern without redeploying code. Configuration is
a JSON-serializable dict (see DEFAULT_PATTERN_CONFIG) stored in SQLite by
handlers.py / database.py and passed into `TossMatcher(config)`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Default pattern configuration
# ---------------------------------------------------------------------------

DEFAULT_PATTERN_CONFIG = {
    # The literal toss phrase. Matched case-insensitively after normalization.
    "toss_phrase": "WON THE TOSS",
    # Connector word between toss phrase and decision.
    "connector": "AND",
    # Accepted decision verbs -> canonical outcome. Each key is matched literally
    # (post-normalization); this is the ONLY place typo variants are listed.
    # Add new variants here, not via fuzzy matching.
    "decision_variants": {
        "DECIDED TO BAT": "BAT",
        "DECIDED TO BOWL": "BOWL",
        "DICIDED TO BAT": "BAT",   # known source typo
        "DICIDED TO BOWL": "BOWL",  # known source typo (symmetrical, in case it appears)
    },
    # Ending marker required after the decision. Two check-mark characters,
    # with tolerant whitespace between/around them.
    "ending_checkmarks": 2,
    # Minimum length of a plausible team identifier (after stripping flags/punctuation).
    "min_team_identifier_len": 2,
    # Maximum allowed characters between the end of the decision phrase and the
    # checkmark ending (guards against trailing unrelated text being appended).
    "max_trailing_gap": 6,
}


CHECKMARK_CHARS = {"\u2714", "\u2705"}  # ✔ (heavy check) and ✅ (white heavy check)
VARIATION_SELECTOR = "\ufe0f"


@dataclass
class MatchResult:
    matched: bool
    reason: str = ""
    team: str | None = None
    decision: str | None = None  # "BAT" or "BOWL"
    toss_phrase_found: bool = False
    connector_found: bool = False
    decision_found: bool = False
    ending_found: bool = False
    normalized_text: str = ""


class TossMatcher:
    def __init__(self, config: dict | None = None):
        self.config = {**DEFAULT_PATTERN_CONFIG, **(config or {})}
        # Sort decision variants longest-first so "DECIDED TO BAT" isn't
        # shadowed by a shorter accidental substring in future configs.
        self._decision_variants = sorted(
            self.config["decision_variants"].items(), key=lambda kv: -len(kv[0])
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_match(self, text: str | None) -> bool:
        try:
            return self.evaluate(text).matched
        except Exception:
            # Fail closed: any unexpected error => no match.
            return False

    def evaluate(self, text: str | None) -> MatchResult:
        """Run full multi-layer validation and return a detailed result.
        Never raises — internal errors are converted into a NO MATCH result.
        """
        try:
            if not text or not isinstance(text, str):
                return MatchResult(matched=False, reason="empty_or_non_text_message")

            normalized = self._normalize(text)
            if not normalized:
                return MatchResult(matched=False, reason="empty_after_normalization")

            # CHECK 2: toss phrase present
            toss_phrase = self.config["toss_phrase"].upper()
            toss_idx = normalized.upper().find(toss_phrase)
            if toss_idx == -1:
                return MatchResult(matched=False, reason="toss_phrase_missing", normalized_text=normalized)

            # CHECK 1: meaningful team identifier before the toss phrase
            team_segment = normalized[:toss_idx]
            team = self._extract_team_identifier(team_segment)
            if not team:
                return MatchResult(
                    matched=False, reason="no_valid_team_identifier",
                    toss_phrase_found=True, normalized_text=normalized,
                )

            after_toss = normalized[toss_idx + len(toss_phrase):]

            # CHECK 3: connector "AND" immediately follows (allowing whitespace)
            connector = self.config["connector"].upper()
            connector_match = re.match(rf"^\s+{re.escape(connector)}\s+", after_toss.upper())
            if not connector_match:
                return MatchResult(
                    matched=False, reason="connector_missing_or_misplaced",
                    team=team, toss_phrase_found=True, normalized_text=normalized,
                )
            remainder = after_toss[connector_match.end():]

            # CHECK 4: decision variant (BAT/BOWL, incl. approved typos) — must
            # start the remainder (no arbitrary text between AND and decision).
            decision_outcome = None
            matched_variant_len = 0
            remainder_upper = remainder.upper()
            for variant, outcome in self._decision_variants:
                if remainder_upper.startswith(variant.upper()):
                    decision_outcome = outcome
                    matched_variant_len = len(variant)
                    break
            if decision_outcome is None:
                return MatchResult(
                    matched=False, reason="decision_phrase_missing_or_unrecognized",
                    team=team, toss_phrase_found=True, connector_found=True,
                    normalized_text=normalized,
                )
            after_decision = remainder[matched_variant_len:]

            # CHECK 5: checkmark ending, tolerant of whitespace, but not
            # arbitrary unrelated trailing text before it.
            ending_ok, gap_reason = self._validate_ending(after_decision)
            if not ending_ok:
                return MatchResult(
                    matched=False, reason=gap_reason,
                    team=team, decision=decision_outcome,
                    toss_phrase_found=True, connector_found=True, decision_found=True,
                    normalized_text=normalized,
                )

            # CHECK 6: overall structural sanity (already enforced by the
            # sequential checks above being contiguous/ordered).
            return MatchResult(
                matched=True,
                reason="ok",
                team=team,
                decision=decision_outcome,
                toss_phrase_found=True,
                connector_found=True,
                decision_found=True,
                ending_found=True,
                normalized_text=normalized,
            )
        except Exception as exc:  # fail closed, never propagate
            return MatchResult(matched=False, reason=f"internal_error:{exc.__class__.__name__}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalize(self, text: str) -> str:
        # Unicode normalize (NFC) so multi-codepoint flag sequences and
        # combining characters compare consistently.
        t = unicodedata.normalize("NFC", text)
        # Normalize line breaks to spaces (message is treated as one line
        # for structural matching; original message is never altered on forward).
        t = t.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        # Collapse repeated whitespace.
        t = re.sub(r"\s+", " ", t)
        # Remove zero-width / invisible characters that could break matching.
        t = "".join(ch for ch in t if unicodedata.category(ch) not in ("Cf",) or ch in CHECKMARK_CHARS)
        return t.strip()

    def _extract_team_identifier(self, segment: str) -> str | None:
        """Strip leading/trailing emoji & punctuation decoration and verify a
        meaningful alphanumeric team name remains. Emoji are optional
        decoration, never the identifying signal."""
        s = segment.strip()
        if not s:
            return None

        # Strip emoji / symbol characters and standalone punctuation from
        # both ends, but keep interior structure (hyphens, spaces, numbers).
        def strip_decoration(s: str) -> str:
            chars = list(s)
            # strip from front
            i = 0
            while i < len(chars) and self._is_decoration_char(chars[i]):
                i += 1
            # strip from back
            j = len(chars)
            while j > i and self._is_decoration_char(chars[j - 1]):
                j -= 1
            return "".join(chars[i:j]).strip()

        core = strip_decoration(s)
        # After stripping outer decoration, also strip any decoration
        # immediately surrounding remaining inner emoji clusters is NOT
        # required — flags may appear on both sides per spec examples
        # ("🇷🇼 RWANDA - U19 🇷🇼"), so re-run strip once more in case an
        # inner flag got exposed after the first pass (defensive, cheap).
        core = strip_decoration(core)

        if len(core) < self.config["min_team_identifier_len"]:
            return None

        # Must contain at least one letter or digit — pure punctuation/emoji
        # leftovers do not count as a team identifier.
        if not re.search(r"[A-Za-z0-9]", core):
            return None

        return core

    @staticmethod
    def _is_decoration_char(ch: str) -> bool:
        if ch in CHECKMARK_CHARS or ch == VARIATION_SELECTOR:
            return True
        if ch.isspace():
            return True
        cat = unicodedata.category(ch)
        # So (Symbol, other) covers most emoji; Sk (modifier symbol); Cs
        # (surrogate, used by some flag/tag sequences); regional indicator
        # letters used for flags fall under 'So' too via categories, but to
        # be safe also treat the Unicode "Regional Indicator Symbol" block
        # and the "Tags" block (used by England/Scotland/Wales flags) as
        # decoration explicitly.
        if cat in ("So", "Sk", "Cs"):
            return True
        codepoint = ord(ch)
        if 0x1F1E6 <= codepoint <= 0x1F1FF:  # regional indicator symbols
            return True
        if 0xE0000 <= codepoint <= 0xE007F:  # tag characters (subdivision flags)
            return True
        # Generic leading punctuation like -, :, |, • used purely as separators
        if cat.startswith("P") and ch not in ("-",):
            # allow hyphen through since it's part of many team names (e.g. "RWANDA - U19")
            return True
        return False

    def _validate_ending(self, tail: str) -> tuple[bool, str]:
        """tail is everything after the matched decision phrase. Require the
        configured number of checkmarks, allowing whitespace before/between,
        but reject if there's meaningful unrelated text in between."""
        remaining = tail
        found = 0
        pos = 0
        max_gap = self.config["max_trailing_gap"]

        # Skip a small amount of whitespace/punctuation before checkmarks,
        # but bail if we encounter substantial unrelated alphanumeric text.
        i = 0
        gap_chars = 0
        while i < len(remaining) and found < self.config["ending_checkmarks"]:
            ch = remaining[i]
            if ch in CHECKMARK_CHARS:
                found += 1
                gap_chars = 0
                i += 1
                continue
            if ch == VARIATION_SELECTOR or ch.isspace():
                i += 1
                continue
            # Any other character counts against the trailing gap budget.
            gap_chars += 1
            if gap_chars > max_gap:
                return False, "unexpected_text_before_ending"
            i += 1

        if found < self.config["ending_checkmarks"]:
            return False, "checkmark_ending_missing"

        # After the required checkmarks, only trivial trailing whitespace /
        # extra checkmarks / variation selectors are allowed. Any other
        # meaningful text after the ending (e.g. promotional junk) must
        # reject the match — the toss declaration must be the end of the
        # message's substantive content.
        trailing = remaining[i:]
        for ch in trailing:
            if ch in CHECKMARK_CHARS or ch == VARIATION_SELECTOR or ch.isspace():
                continue
            return False, "unexpected_text_after_ending"

        return True, "ok"
