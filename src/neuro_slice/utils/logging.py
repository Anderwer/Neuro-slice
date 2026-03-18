from __future__ import annotations

import logging
from typing import Optional

try:
    from rich.console import Console
    from rich.logging import RichHandler
except Exception:  # pragma: no cover - optional dependency fallback
    Console = None
    RichHandler = None


DEFAULT_LOG_FORMAT = "%(message)s"
DEFAULT_DATE_FORMAT = "[%X]"


def _normalize_level(level: str | int) -> int:
    if isinstance(level, int):
        return level

    normalized = level.strip().upper()
    mapping = {
        "CRITICAL": logging.CRITICAL,
        "ERROR": logging.ERROR,
        "WARNING": logging.WARNING,
        "WARN": logging.WARNING,
        "INFO": logging.INFO,
        "DEBUG": logging.DEBUG,
        "NOTSET": logging.NOTSET,
    }
    return mapping.get(normalized, logging.INFO)


def configure_logging(
    level: str | int = "INFO",
    *,
    use_rich: bool = True,
    logger_name: Optional[str] = None,
) -> logging.Logger:
    """
    Configure and return an application logger.

    Parameters
    ----------
    level:
        Logging level name or numeric level.
    use_rich:
        Whether to prefer rich logging output when available.
    logger_name:
        Specific logger name. If omitted, configure the root logger.

    Returns
    -------
    logging.Logger
        Configured logger instance.
    """
    log_level = _normalize_level(level)
    logger = logging.getLogger(logger_name)
    logger.setLevel(log_level)
    logger.propagate = False

    # Clear existing handlers to avoid duplicate logs when CLI commands
    # configure logging more than once in a single process.
    logger.handlers.clear()

    if use_rich and RichHandler is not None and Console is not None:
        console = Console(stderr=True)
        handler: logging.Handler = RichHandler(
            console=console,
            rich_tracebacks=True,
            show_time=True,
            show_level=True,
            show_path=False,
            markup=True,
        )
        formatter = logging.Formatter(DEFAULT_LOG_FORMAT, datefmt=DEFAULT_DATE_FORMAT)
    else:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )

    handler.setLevel(log_level)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


def get_logger(name: str, *, level: str | int | None = None) -> logging.Logger:
    """
    Return a logger, optionally ensuring it has at least a basic handler.
    """
    logger = logging.getLogger(name)

    if level is not None:
        logger.setLevel(_normalize_level(level))

    if not logger.handlers and not logger.propagate:
        configure_logging(level=level or "INFO", logger_name=name)

    return logger