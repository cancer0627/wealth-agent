"""Non-fatal structured method logging and correlation context."""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, ParamSpec, Protocol, TypeVar

from pydantic import BaseModel

from wealth_agent.shared.identity import new_id

P = ParamSpec("P")
R = TypeVar("R")
T = TypeVar("T", bound=type[Any])

MAX_STRING_LENGTH = 4_000
MAX_COLLECTION_ITEMS = 100
MAX_DEPTH = 8
MAX_NODES = 2_000
_IDENTIFIER_KEYS = (
    "task_id",
    "correlation_id",
    "request_id",
    "agent_run_id",
    "tool_call_id",
)


class EventSink(Protocol):
    def emit(self, event: Mapping[str, object]) -> None: ...


class _NullSink:
    def emit(self, event: Mapping[str, object]) -> None:
        del event


class _SerializationBudget:
    def __init__(self) -> None:
        self.nodes = 0
        self.seen: set[int] = set()

    def consume(self) -> bool:
        self.nodes += 1
        return self.nodes <= MAX_NODES


_SINK: EventSink = _NullSink()
_SINK_LOCK = RLock()
_TRACE_ID: ContextVar[str | None] = ContextVar("wealth_agent_trace_id", default=None)
_CALL_STACK: ContextVar[tuple[str, ...]] = ContextVar(
    "wealth_agent_call_stack", default=()
)


def configure_logging(sink: EventSink | None) -> None:
    global _SINK
    with _SINK_LOCK:
        _SINK = sink or _NullSink()


def current_trace_id() -> str:
    trace_id = _TRACE_ID.get()
    if trace_id is None:
        trace_id = new_id("trace")
        _TRACE_ID.set(trace_id)
    return trace_id


@contextmanager
def trace_context(trace_id: str | None = None) -> Iterator[str]:
    resolved = trace_id or new_id("trace")
    trace_token = _TRACE_ID.set(resolved)
    stack_token = _CALL_STACK.set(())
    try:
        yield resolved
    finally:
        _CALL_STACK.reset(stack_token)
        _TRACE_ID.reset(trace_token)


def serialize_value(value: object) -> object:
    try:
        return _serialize(value, depth=0, budget=_SerializationBudget())
    except Exception as exc:
        return {
            "truncated": True,
            "reason": "serialization_error",
            "error_type": type(exc).__name__,
        }


def _serialize(value: object, *, depth: int, budget: _SerializationBudget) -> object:
    if not budget.consume():
        return {"truncated": True, "reason": "node_limit"}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) <= MAX_STRING_LENGTH:
            return value
        return {
            "value": value[:MAX_STRING_LENGTH],
            "truncated": True,
            "original_length": len(value),
            "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        }
    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "length": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, Path, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    if depth >= MAX_DEPTH:
        return {
            "truncated": True,
            "reason": "depth_limit",
            "type": type(value).__name__,
        }

    track_identity = isinstance(value, (Mapping, list, tuple, set, BaseModel)) or (
        is_dataclass(value) and not isinstance(value, type)
    )
    identity = id(value)
    if track_identity:
        if identity in budget.seen:
            return {"truncated": True, "reason": "cycle", "type": type(value).__name__}
        budget.seen.add(identity)
    try:
        if isinstance(value, BaseModel):
            return _serialize(
                value.model_dump(mode="python"), depth=depth + 1, budget=budget
            )
        if is_dataclass(value) and not isinstance(value, type):
            return _serialize(
                {field.name: getattr(value, field.name) for field in fields(value)},
                depth=depth + 1,
                budget=budget,
            )
        if isinstance(value, Mapping):
            items = list(value.items())
            mapping_result = {
                str(key): _serialize(item, depth=depth + 1, budget=budget)
                for key, item in items[:MAX_COLLECTION_ITEMS]
            }
            if len(items) > MAX_COLLECTION_ITEMS:
                mapping_result["_truncated"] = {
                    "truncated": True,
                    "original_length": len(items),
                }
            return mapping_result
        if isinstance(value, (list, tuple, set)):
            items = list(value)
            collection_result = [
                _serialize(item, depth=depth + 1, budget=budget)
                for item in items[:MAX_COLLECTION_ITEMS]
            ]
            if len(items) > MAX_COLLECTION_ITEMS:
                collection_result.append(
                    {"truncated": True, "original_length": len(items)}
                )
            return collection_result
        if isinstance(value, BaseException):
            detail = getattr(value, "detail", None)
            return {
                "type": type(value).__name__,
                "message": str(value),
                "detail": _serialize(detail, depth=depth + 1, budget=budget),
            }
        try:
            json.dumps(value)
        except (TypeError, ValueError, OverflowError):
            return {"type": type(value).__name__, "repr": repr(value)[:MAX_STRING_LENGTH]}
        return value
    finally:
        if track_identity:
            budget.seen.discard(identity)


def trace_call(
    *,
    layer: str,
    trace_resolver: Callable[..., str | None] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        if getattr(function, "__wealth_agent_traced__", False):
            return function
        signature = inspect.signature(function)

        @functools.wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            resolved_trace = _resolve_trace(trace_resolver, args, kwargs)
            trace_token = None
            active_trace = _TRACE_ID.get()
            if resolved_trace is not None and resolved_trace != active_trace:
                trace_token = _TRACE_ID.set(resolved_trace)
                active_trace = resolved_trace
            elif active_trace is None:
                active_trace = new_id("trace")
                trace_token = _TRACE_ID.set(active_trace)
            trace_id = active_trace
            call_id = new_id("call")
            stack = _CALL_STACK.get()
            parent_call_id = stack[-1] if stack else None
            stack_token = _CALL_STACK.set((*stack, call_id))
            inputs = _bound_inputs(signature, args, kwargs)
            identifiers = _extract_identifiers(inputs)
            common = {
                "schema_version": 1,
                "layer": layer,
                "component": _component(function),
                "method": function.__qualname__,
                "call_id": call_id,
                "parent_call_id": parent_call_id,
                **identifiers,
                "trace_id": trace_id,
            }
            started_at = time.perf_counter()
            _emit({**common, "level": "INFO", "event": "method_call", "input": inputs})
            try:
                result = function(*args, **kwargs)
            except BaseException as exc:
                duration_ms = round((time.perf_counter() - started_at) * 1_000, 3)
                _emit(
                    {
                        **common,
                        "level": "ERROR",
                        "event": "method_error",
                        "duration_ms": duration_ms,
                        "error": exc,
                    }
                )
                raise
            else:
                duration_ms = round((time.perf_counter() - started_at) * 1_000, 3)
                output_identifiers = {
                    key: value
                    for key, value in _extract_identifiers(result).items()
                    if value is not None and key != "trace_id"
                }
                _emit(
                    {
                        **common,
                        **output_identifiers,
                        "level": "INFO",
                        "event": "method_return",
                        "duration_ms": duration_ms,
                        "output": result,
                    }
                )
                return result
            finally:
                _CALL_STACK.reset(stack_token)
                if trace_token is not None:
                    _TRACE_ID.reset(trace_token)

        wrapped.__dict__["__wealth_agent_traced__"] = True
        return wrapped

    return decorate


def trace_public_methods(*, layer: str) -> Callable[[T], T]:
    def decorate(cls: T) -> T:
        for name, member in tuple(vars(cls).items()):
            if name.startswith("_") or not inspect.isfunction(member):
                continue
            setattr(cls, name, trace_call(layer=layer)(member))
        return cls

    return decorate


def _resolve_trace(
    resolver: Callable[..., str | None] | None,
    args: tuple[object, ...],
    kwargs: Mapping[str, object],
) -> str | None:
    if resolver is not None:
        try:
            resolved = resolver(*args, **kwargs)
            if resolved:
                return resolved
        except Exception:
            pass
    identifiers = _extract_identifiers({"args": args, "kwargs": kwargs})
    trace_id = identifiers.get("trace_id")
    return trace_id if isinstance(trace_id, str) else None


def _bound_inputs(
    signature: inspect.Signature,
    args: tuple[object, ...],
    kwargs: Mapping[str, object],
) -> object:
    try:
        bound = signature.bind_partial(*args, **kwargs)
        values = {
            name: value
            for name, value in bound.arguments.items()
            if name not in {"self", "cls"}
        }
    except TypeError:
        values = {"args": args[1:] if args else args, "kwargs": kwargs}
    return serialize_value(values)


def _extract_identifiers(value: object) -> dict[str, str | None]:
    found: dict[str, str | None] = {key: None for key in _IDENTIFIER_KEYS}
    found["trace_id"] = None

    def visit(item: object, depth: int) -> None:
        if depth > 5 or all(result is not None for result in found.values()):
            return
        if isinstance(item, BaseModel):
            visit(item.model_dump(mode="python"), depth + 1)
            return
        if is_dataclass(item) and not isinstance(item, type):
            visit({field.name: getattr(item, field.name) for field in fields(item)}, depth + 1)
            return
        if isinstance(item, Mapping):
            for key, nested in item.items():
                text_key = str(key)
                if text_key in found and found[text_key] is None and isinstance(nested, str):
                    found[text_key] = nested
                if depth < 5:
                    visit(nested, depth + 1)
            return
        if isinstance(item, (list, tuple)):
            for nested in item[:20]:
                visit(nested, depth + 1)

    try:
        visit(value, 0)
    except Exception:
        return found
    return found


def _component(function: Callable[..., object]) -> str:
    module = function.__module__
    for marker in (".adapters.", ".capabilities."):
        if marker in module:
            return module.split(marker, maxsplit=1)[1]
    return module


def _emit(event: Mapping[str, object]) -> None:
    payload = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        **{key: serialize_value(value) for key, value in event.items()},
    }
    try:
        with _SINK_LOCK:
            sink = _SINK
        sink.emit(payload)
    except Exception as exc:
        print(
            f"wealth-agent logging degraded: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )


__all__ = [
    "EventSink",
    "configure_logging",
    "current_trace_id",
    "serialize_value",
    "trace_call",
    "trace_context",
    "trace_public_methods",
]
