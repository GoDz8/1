"""Structured logging for AXIOM (spec §0: everything is logged).

The SQLite audit trail is the authoritative record; this logger is the
human-readable companion stream. Console + rotating file.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

_CONFIGURED = False
_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def setup_logging(level: int = logging.INFO, log_file: str | None = "axiom.log") -> None:
    """Configure the root 'axiom' logger once (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    logger = logging.getLogger("axiom")
    logger.setLevel(level)
    logger.propagate = False

    fmt = logging.Formatter(_FORMAT)

    console = logging.StreamHandler(stream=sys.stderr)
    console.setFormatter(fmt)
    logger.addHandler(console)

    if log_file:
        fileh = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5)
        fileh.setFormatter(fmt)
        logger.addHandler(fileh)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced child of the 'axiom' logger."""
    if not _CONFIGURED:
        setup_logging()
    return logging.getLogger(f"axiom.{name}")
