"""本地进程间文件锁公共导出。"""

from wealth_agent.shared.file_lock.local import FileLock, FileLockTimeoutError

__all__ = ["FileLock", "FileLockTimeoutError"]
