"""Thread-safe daily JSON Lines sink with bounded event size."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from threading import RLock

MAX_EVENT_BYTES = 256 * 1024


class DailyJsonlSink:
    def __init__(
        self,
        logs_dir: Path,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._logs_dir = logs_dir
        self._now = now or (lambda: datetime.now().astimezone())
        self._lock = RLock()

    def emit(self, event: Mapping[str, object]) -> None:
        payload = dict(event)
        line = self._encode(payload)
        if len(line) > MAX_EVENT_BYTES:
            payload = self._bounded_fallback(payload, line)
            line = self._encode(payload)
        with self._lock:
            self._logs_dir.mkdir(parents=True, exist_ok=True)
            path = self._logs_dir / f"wealth-agent-{self._now().date().isoformat()}.jsonl"
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line.decode("utf-8"))
                stream.write("\n")

    @staticmethod
    def _encode(payload: Mapping[str, object]) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @staticmethod
    def _bounded_fallback(payload: dict[str, object], original: bytes) -> dict[str, object]:
        bounded = {
            key: value
            for key, value in payload.items()
            if key not in {"input", "output", "error"}
        }
        bounded["payload"] = {
            "truncated": True,
            "reason": "event_size_limit",
            "original_bytes": len(original),
            "sha256": hashlib.sha256(original).hexdigest(),
        }
        return bounded


__all__ = ["DailyJsonlSink"]
