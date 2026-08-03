from __future__ import annotations

import fcntl
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging() -> Path:
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    log_dir = cache_home / "quota-ring"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "quota-ring.log"
    root = logging.getLogger()
    for existing in root.handlers:
        if getattr(existing, "quota_ring_handler", False):
            return log_path
    handler = RotatingFileHandler(
        log_path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    handler.quota_ring_handler = True  # type: ignore[attr-defined]
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return log_path


class InstanceLock:
    def __init__(self, path: Path | None = None):
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        self.path = path or (
            Path(runtime_dir) / "quota-ring.lock"
            if runtime_dir
            else cache_home / "quota-ring" / "quota-ring.lock"
        )
        self._descriptor: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path,
            os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return False
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode())
        self._descriptor = descriptor
        return True

    def release(self) -> None:
        if self._descriptor is None:
            return
        fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        os.close(self._descriptor)
        self._descriptor = None
