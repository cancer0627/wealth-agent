"""基于 fcntl 的本地进程间文件锁，不包含业务语义。"""

from __future__ import annotations

import fcntl
import time
from pathlib import Path
from types import TracebackType
from typing import BinaryIO


class FileLockTimeoutError(TimeoutError):
    pass


class FileLock:
    def __init__(self, path: Path, *, exclusive: bool, timeout_seconds: float = 5) -> None:
        self._path = path
        self._exclusive = exclusive
        self._timeout_seconds = timeout_seconds
        self._handle: BinaryIO | None = None

    def __enter__(self) -> FileLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+b")
        operation = fcntl.LOCK_EX if self._exclusive else fcntl.LOCK_SH
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            try:
                fcntl.flock(handle.fileno(), operation | fcntl.LOCK_NB)
                self._handle = handle
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise FileLockTimeoutError("无法取得文件锁，请稍后重试") from None
                time.sleep(0.05)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
