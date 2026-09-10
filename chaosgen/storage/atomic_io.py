"""
Dependency-free atomic file helpers (Windows-safe retries).

PromotedStore uses read-merge-write + replace (whole JSON document).
Audit JSONL must NOT use that pattern — see append_jsonl_line().
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)


def replace_with_retry(
    src: Path | str,
    dst: Path | str,
    *,
    attempts: int = 16,
    base_delay: float = 0.05,
) -> None:
    """``os.replace`` with backoff for Windows WinError 5/32 sharing races."""
    src_s, dst_s = str(src), str(dst)
    last: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            os.replace(src_s, dst_s)
            return
        except PermissionError as exc:
            last = exc
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            if winerror not in (5, 32) and exc.errno not in (13, 11, 16):
                raise
            last = exc
        time.sleep(base_delay * (attempt + 1))
    assert last is not None
    raise last


def read_text_with_retry(
    path: Path | str,
    *,
    encoding: str = "utf-8",
    attempts: int = 16,
    base_delay: float = 0.02,
) -> str:
    """
    Read a file tolerating Windows sharing races (WinError 5/32).

    Right after ``os.replace`` the new file can be briefly un-openable while the
    old handle is in pending-delete state or an AV scanner holds it. Callers must
    not mistake that for corruption and fall back to a stale backup.
    """
    target = Path(path)
    last: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            return target.read_text(encoding=encoding)
        except FileNotFoundError:
            raise
        except PermissionError as exc:
            last = exc
        except OSError as exc:
            if getattr(exc, "winerror", None) not in (5, 32) and exc.errno not in (
                13,
                11,
                16,
            ):
                raise
            last = exc
        time.sleep(base_delay * (attempt + 1))
    assert last is not None
    raise last


class ExclusiveFileLock:
    """
    Cross-process advisory lock via ``O_CREAT|O_EXCL`` lockfile.

    Never proceeds unlocked: on timeout raises ``TimeoutError``.
    """

    def __init__(self, lock_path: Path | str, *, timeout: float = 30.0, poll: float = 0.05) -> None:
        self.lock_path = Path(lock_path)
        self.timeout = timeout
        self.poll = poll
        self._fd: Optional[int] = None

    def __enter__(self) -> "ExclusiveFileLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._fd = os.open(
                    str(self.lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_RDWR,
                )
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                return self
            except FileExistsError:
                pass
            except PermissionError:
                # Windows reports ERROR_ACCESS_DENIED (not FileExistsError) while
                # the lock file sits in pending-delete state after a release.
                pass
            except OSError as exc:
                if getattr(exc, "winerror", None) not in (5, 32):
                    raise
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"file lock busy after {self.timeout:.1f}s: {self.lock_path}"
                )
            time.sleep(self.poll)

    def __exit__(self, *exc: Any) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        # A failed unlink would strand every other writer until its timeout.
        for attempt in range(5):
            try:
                self.lock_path.unlink()
                return
            except FileNotFoundError:
                return
            except OSError:
                time.sleep(self.poll * (attempt + 1))
        logger.warning("Could not remove lock file %s", self.lock_path)


def append_jsonl_line(
    path: Path | str,
    record: dict[str, Any],
    *,
    lock_path: Path | str | None = None,
    lock_timeout: float = 30.0,
) -> None:
    """
    Append-only JSONL write (audit SOT).

    Differs from PromotedStore:
    - Does **not** rewrite the whole file
    - Does **not** call ``os.replace`` on the live JSONL path
    - Serializes writers with ExclusiveFileLock, then ``O_APPEND`` + flush + fsync

    This avoids the Windows replace/sharing race that can drop PromotedStore records.
    """
    target = Path(path)
    lock = Path(lock_path) if lock_path else target.with_name(target.name + ".lock")
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    payload = line.encode("utf-8")

    with ExclusiveFileLock(lock, timeout=lock_timeout):
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            written = 0
            while written < len(payload):
                n = os.write(fd, payload[written:])
                if n <= 0:
                    raise OSError("short write to JSONL audit file")
                written += n
            os.fsync(fd)
        finally:
            os.close(fd)


def iter_jsonl(path: Path | str) -> Iterator[dict[str, Any]]:
    """
    Yield parsed JSON objects from a JSONL file (A9 — torn-tail tolerant).

    A crash or power loss can leave a partially written last line. Readers must
    surface every intact record instead of failing the whole file, so blank,
    malformed, and non-object lines are skipped with a warning.
    """
    target = Path(path)
    if not target.exists():
        return
    with target.open("r", encoding="utf-8", errors="replace") as handle:
        for lineno, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning(
                    "Skipping corrupt JSONL line %s:%d (%s)", target, lineno, exc
                )
                continue
            if not isinstance(record, dict):
                logger.warning(
                    "Skipping non-object JSONL line %s:%d (%s)",
                    target,
                    lineno,
                    type(record).__name__,
                )
                continue
            yield record
