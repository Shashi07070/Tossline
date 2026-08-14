"""
logger.py — Local logging setup.

Never logs secrets (BOT_TOKEN, API_HASH, OTP, 2FA password, session data).
Callers must never pass those values into log calls — this module does not
attempt to scrub arbitrary strings, so keep secrets out of log call sites.
"""

import logging
import sys


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

    return logger
