"""Logging setup with secret redaction."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.config import get_config

_REDACT = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|Bearer\s+[A-Za-z0-9._\-]+|api[_-]?key=\S+)", re.IGNORECASE)
_configured = False


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        if _REDACT.search(message):
            record.msg = _REDACT.sub("[REDACTED]", message)
            record.args = ()
        return True


def setup_logging(level: str | None = None) -> logging.Logger:
    """Idempotently configure console + rotating file logging."""
    global _configured
    logger = logging.getLogger("aurelia")
    if _configured:
        return logger

    config = get_config()
    resolved = (level or config.logging.level or "INFO").upper()
    logger.setLevel(getattr(logging, resolved, logging.INFO))
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s")
    secret_filter = SecretFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(secret_filter)
    logger.addHandler(console)

    try:
        log_path = Path(config.data_root) / "logs" / "app.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(secret_filter)
        logger.addHandler(file_handler)
    except OSError:  # pragma: no cover - read-only filesystem
        logger.warning("File logging unavailable; continuing with console logging only.")

    _configured = True
    return logger


def get_logger(name: str = "aurelia") -> logging.Logger:
    root = setup_logging()
    return root if name in {"aurelia", ""} else root.getChild(name)
