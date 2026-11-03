from __future__ import annotations

import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel
from wealth_agent.adapters.logging import DailyJsonlSink
from wealth_agent.shared.logging import (
    configure_logging,
    serialize_value,
    trace_call,
    trace_context,
    trace_public_methods,
)


class MemorySink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def emit(self, event: Mapping[str, object]) -> None:
        self.events.append(dict(event))


class FailingSink:
    def emit(self, event: Mapping[str, object]) -> None:
        del event
        raise OSError("disk unavailable")


class ExampleModel(BaseModel):
    token: str
    amount: Decimal
    path: Path
    content: bytes


class BrokenRepr:
    def __repr__(self) -> str:
        raise RuntimeError("repr failed")


def test_serializer_keeps_business_fields_and_bounds_large_values() -> None:
    payload = serialize_value(
        ExampleModel(
            token="business-token",
            amount=Decimal("12.34"),
            path=Path("data/example.db"),
            content=b"binary-content",
        )
    )
    assert isinstance(payload, dict)
    assert payload["token"] == "business-token"
    assert payload["amount"] == "12.34"
    assert payload["content"] == {
        "type": "bytes",
        "length": 14,
        "sha256": "37456ce54a2ef39b6c9c1d96ddc978f2edc730744bd2c9872dc1cc9ac886b00e",
    }

    long_value = serialize_value("x" * 4_001)
    assert isinstance(long_value, dict)
    assert long_value["truncated"] is True
    assert long_value["original_length"] == 4_001

    collection = serialize_value(list(range(101)))
    assert isinstance(collection, list)
    assert len(collection) == 101
    assert collection[-1] == {"truncated": True, "original_length": 101}

    cyclic: list[object] = []
    cyclic.append(cyclic)
    assert serialize_value(cyclic) == [
        {"truncated": True, "reason": "cycle", "type": "list"}
    ]
    assert serialize_value(BrokenRepr()) == {
        "truncated": True,
        "reason": "serialization_error",
        "error_type": "RuntimeError",
    }


def test_trace_call_records_nested_call_tree_and_preserves_errors(capsys: object) -> None:
    sink = MemorySink()
    configure_logging(sink)

    @trace_public_methods(layer="capability")
    class Service:
        def outer(self, task_id: str) -> str:
            return self.inner(task_id)

        def inner(self, task_id: str) -> str:
            return f"done:{task_id}"

        def _private(self) -> None:
            raise AssertionError("私有方法不应被装饰")

    with trace_context("trace-test"):
        assert Service().outer("task-test") == "done:task-test"

    calls = [event for event in sink.events if event["event"] == "method_call"]
    assert len(calls) == 2
    assert calls[0]["trace_id"] == calls[1]["trace_id"] == "trace-test"
    assert calls[0]["task_id"] == calls[1]["task_id"] == "task-test"
    assert calls[1]["parent_call_id"] == calls[0]["call_id"]

    configure_logging(FailingSink())

    @trace_call(layer="capability")
    def unchanged() -> str:
        return "ok"

    assert unchanged() == "ok"
    configure_logging(None)
    assert "logging degraded" in capsys.readouterr().err  # type: ignore[attr-defined]


def test_daily_sink_switches_files_keeps_history_and_is_thread_safe(tmp_path: Path) -> None:
    current = [datetime(2026, 9, 14, 23, 59, tzinfo=UTC)]
    sink = DailyJsonlSink(tmp_path, now=lambda: current[0])
    old = tmp_path / "wealth-agent-2026-09-13.jsonl"
    old.write_text('{"old":true}\n', encoding="utf-8")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda index: sink.emit({"index": index}), range(50)))
    current[0] = datetime(2026, 9, 15, tzinfo=UTC)
    sink.emit({"index": 50})

    first = tmp_path / "wealth-agent-2026-09-14.jsonl"
    second = tmp_path / "wealth-agent-2026-09-15.jsonl"
    assert len(first.read_text(encoding="utf-8").splitlines()) == 50
    assert json.loads(second.read_text(encoding="utf-8"))["index"] == 50
    assert old.is_file()


def test_daily_sink_replaces_oversized_payload_with_hash(tmp_path: Path) -> None:
    sink = DailyJsonlSink(
        tmp_path,
        now=lambda: datetime(2026, 9, 14, tzinfo=UTC),
    )
    sink.emit({"trace_id": "trace-large", "input": "x" * (300 * 1024)})

    event = json.loads(
        (tmp_path / "wealth-agent-2026-09-14.jsonl").read_text(encoding="utf-8")
    )
    assert event["trace_id"] == "trace-large"
    assert event["payload"]["truncated"] is True
    assert event["payload"]["original_bytes"] > 256 * 1024
