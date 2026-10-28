"""JSON log file plus a short human line on stderr."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "event_fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        fields = getattr(record, "event_fields", None)
        if not isinstance(fields, dict) or not fields:
            return base
        extra = " ".join(f"{key}={fields[key]}" for key in fields)
        return f"{base} {extra}"


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    logger.log(level, message, extra={"event_fields": fields})


def configure_logging(log_dir: Path | None, level_name: str, *, also_stderr: bool = True) -> None:
    level = getattr(logging, level_name.upper(), None)
    if not isinstance(level, int):
        level = logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    if also_stderr:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(HumanFormatter("%(asctime)s %(levelname)s %(message)s"))
        root.addHandler(stream)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "meet-notes.jsonl"
        file_handler = RotatingFileHandler(
            path,
            maxBytes=5_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)


def get_logger(name: str = "meet_notes") -> logging.Logger:
    return logging.getLogger(name)
