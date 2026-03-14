"""
TASO – Structured logging subsystem.

Uses loguru for all logging.  One rotating file per log category is
created under logs/; a single combined sink is also maintained.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from loguru import logger

from config.settings import settings

_initialised = False


def _fmt(category: str) -> str:
    return (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        f"<level>{{level: <8}}</level> | "
        f"<cyan>{category}</cyan> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> – "
        "<level>{message}</level>"
    )


def _file(name: str) -> Path:
    return settings.LOG_DIR / f"{name}.log"


def init_logging() -> None:
    """Initialise all log sinks exactly once."""
    global _initialised
    if _initialised:
        return
    _initialised = True

    logger.remove()  # remove default stderr sink

    # Pretty stderr sink
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL,
        colorize=True,
        format=_fmt("stderr"),
        backtrace=True,
        diagnose=True,
    )

    # Per-category rotating file sinks
    categories = [
        "agent",
        "tool",
        "security",
        "self_improvement",
        "error",
        "combined",
    ]
    for cat in categories:
        logger.add(
            str(_file(cat)),
            level="DEBUG",
            rotation=settings.LOG_ROTATION,
            retention=10,
            compression="zip",
            format=_fmt(cat),
            filter=lambda rec, c=cat: rec["extra"].get("category", "combined") in (c, "combined"),
            backtrace=True,
            diagnose=True,
        )

    logger.info("Logging initialised", category="combined")


def get_logger(category: str = "combined"):
    """Return a loguru logger bound to *category*."""
    return logger.bind(category=category)


# Convenience loggers used throughout the codebase
agent_log = get_logger("agent")
tool_log = get_logger("tool")
security_log = get_logger("security")
self_improvement_log = get_logger("self_improvement")
error_log = get_logger("error")
