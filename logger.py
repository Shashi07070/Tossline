"""
logger.py — Local logging setup.

Never logs secrets (BOT_TOKEN, API_HASH, OTP, 2FA password, session data).
Callers must never pass those values into log calls — this module does not
attempt to scrub arbitrary strings, so keep secrets out of log call sites.
"""

import collections
import logging
import sys
import threading
import time

# In-memory ring buffer of recent ERROR+ log records, for the admin /errors
# command. Deliberately NOT written through the normal formatter so we can
# keep (timestamp, message) structured — and deliberately in-memory only
# (never persisted) since log lines could theoretically contain exception
# text; the file/stream handlers below still capture everything to LOG_PATH
# as before, this is purely an additional tap for the bot's own command.
_ERROR_BUFFER = collections.deque(maxlen=200)
_ERROR_LOCK = threading.Lock()


class _ErrorBufferHandler(logging.Handler):
    """Captures ERROR-level-and-above records into an in-memory ring buffer
    so the admin bot can show recent errors without reading the log file."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        with _ERROR_LOCK:
            _ERROR_BUFFER.append((time.time(), msg))


def get_recent_errors(limit: int = 10):
    """Returns up to `limit` most recent (timestamp, message) error entries,
    newest first."""
    with _ERROR_LOCK:
        items = list(_ERROR_BUFFER)[-limit:]
    return list(reversed(items))


def setup_logger(name: str, log_path: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    error_handler = _ErrorBufferHandler(level=logging.ERROR)
    error_handler.setFormatter(fmt)
    logger.addHandler(error_handler)

    return logger
