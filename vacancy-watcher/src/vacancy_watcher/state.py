"""Small atomic JSON state store; it never contains credentials or page URLs."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any


DEFAULT_STATE: dict[str, Any] = {
    "schema": 1,
    "vacancy_status": "unknown",
    "notifications": {},
    "failure_count": 0,
    "enrollment_status": "idle",
    "manual_intervention": False,
    "last_cycle_epoch": 0,
}


class StateError(RuntimeError):
    """Raised when persistent state is missing or not valid JSON."""


class LockHeldError(RuntimeError):
    """Raised when another process owns a non-blocking watcher lock."""


class ExclusiveFileLock:
    """Small cross-platform advisory lock released automatically on process exit."""

    def __init__(self, path: Path, *, blocking: bool = False):
        self.path = Path(path)
        self.blocking = blocking
        self.handle: Any | None = None

    def __enter__(self) -> "ExclusiveFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        if self.handle.tell() == 0:
            self.handle.write(b"\0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                mode = msvcrt.LK_LOCK if self.blocking else msvcrt.LK_NBLCK
                msvcrt.locking(self.handle.fileno(), mode, 1)
            else:
                import fcntl

                mode = fcntl.LOCK_EX if self.blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
                fcntl.flock(self.handle.fileno(), mode)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise LockHeldError(f"lock is already held: {self.path.name}") from exc
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def _restrict(path: Path) -> None:
    """Best-effort owner-only permissions (POSIX); harmless on Windows mounts."""

    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def write_json_secure_atomic(path: Path, value: dict[str, Any]) -> None:
    """Write JSON by owner-only temporary file and atomic replacement."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        _restrict(temporary_path)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        _restrict(path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


class AtomicState:
    def __init__(self, path: Path):
        self.path = Path(path)

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return json.loads(json.dumps(DEFAULT_STATE))
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError("state file cannot be read safely") from exc
        if not isinstance(value, dict) or value.get("schema") != 1:
            raise StateError("state file schema is unsupported")
        merged = json.loads(json.dumps(DEFAULT_STATE))
        merged.update(value)
        if not isinstance(merged.get("notifications"), dict):
            raise StateError("state notifications field is invalid")
        return merged

    def write(self, value: dict[str, Any]) -> None:
        if not isinstance(value, dict):
            raise TypeError("state must be a mapping")
        write_json_secure_atomic(self.path, value)

    def update(self, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        with ExclusiveFileLock(lock_path, blocking=True):
            state = self.read()
            mutate(state)
            self.write(state)
            return state

    def claim_notification(self, key: str) -> bool:
        """Atomically record a notification key and return whether it was new."""

        claimed = False

        def mutate(state: dict[str, Any]) -> None:
            nonlocal claimed
            notifications = state["notifications"]
            if key not in notifications:
                notifications[key] = int(time.time())
                claimed = True

        self.update(mutate)
        return claimed

    def transition_vacancy_status(self, status: str) -> str:
        """Update status and re-arm the opposite transition notification."""

        previous = "unknown"

        def mutate(state: dict[str, Any]) -> None:
            nonlocal previous
            previous = str(state.get("vacancy_status", "unknown"))
            state["vacancy_status"] = status
            if previous == status:
                return
            notifications = state["notifications"]
            if status != "available":
                notifications.pop("vacancy:available", None)
            if status != "unavailable":
                notifications.pop("vacancy:unavailable", None)

        self.update(mutate)
        return previous

    def latch_manual_intervention(self, status: str = "uncertain") -> None:
        def mutate(state: dict[str, Any]) -> None:
            state["manual_intervention"] = True
            state["enrollment_status"] = status

        self.update(mutate)

    def mark_submission_started(self) -> None:
        """Persist the point after which a crash must never trigger an automatic retry."""

        self.update(lambda state: state.__setitem__("enrollment_status", "submitting"))
