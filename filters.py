import re
import logging

logger = logging.getLogger(__name__)

def contains_url(text: str) -> bool:
    """Check if text contains URLs"""
    url_pattern = re.compile(
        r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
    )
    return bool(url_pattern.search(text))

def contains_blacklisted_keyword(text: str, blacklist: list) -> bool:
    """Check if text contains blacklisted keywords"""
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in blacklist)

def should_forward_message(message, blacklist: list) -> bool:
    """
    Determine if message should be forwarded
    
    Filters:
    - Must be text-only (no media)
    - Must not contain URLs
    - Must not contain blacklisted keywords
    """
    
    # Must have text
    if not message.text:
        logger.debug("Message has no text - blocking")
        return False
    
    # Check for URLs
    if contains_url(message.text):
        logger.debug("Message contains URL - blocking")
        return False
    
    # Check blacklist
    if contains_blacklisted_keyword(message.text, blacklist):
        logger.debug("Message contains blacklisted keyword - blocking")
        return False
    
    return True
