"""Self-contained message filtering for Toss Forward Bot."""
from dataclasses import dataclass
import re

_URL_RE = re.compile(
    r"""(?ix)
    (?:https?://|www\.)[^\s<>"']+
    |
    (?:t\.me/|telegram\.me/|telegram\.dog/)[^\s<>"']+
    |
    \b(?:[a-z0-9-]+\.)+(?:com|net|org|io|co|in|bet|win|xyz|me|tv|site|online)
    (?:/[^\s<>"']*)?
    """
)

@dataclass(frozen=True)
class FilterResult:
    allowed: bool
    reason: str

def contains_link(text):
    if not text:
        return False
    return bool(_URL_RE.search(str(text)))

def contains_blacklisted_term(text, blacklist):
    if not text:
        return None
    lowered = str(text).casefold()
    for term in blacklist or []:
        term = str(term).strip()
        if term and term.casefold() in lowered:
            return term
    return None

def is_text_only_message(message):
    try:
        text = getattr(message, "raw_text", None)
        if not isinstance(text, str) or not text.strip():
            return False
        if getattr(message, "media", None) is not None:
            return False
        blocked = (
            "photo", "video", "gif", "sticker", "voice", "video_note",
            "audio", "document", "contact", "geo", "venue", "poll", "game"
        )
        return not any(getattr(message, attr, None) is not None for attr in blocked)
    except Exception:
        return False

def evaluate_message(message, blacklist=()):
    try:
        if not is_text_only_message(message):
            return FilterResult(False, "non_text_or_media_message")
        text = getattr(message, "raw_text", None) or ""
        if contains_link(text):
            return FilterResult(False, "contains_link")
        matched = contains_blacklisted_term(text, blacklist)
        if matched is not None:
            return FilterResult(False, f"blacklisted_term:{matched}")
        return FilterResult(True, "allowed")
    except Exception:
        return FilterResult(False, "filter_error")
