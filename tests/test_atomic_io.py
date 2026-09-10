"""Tests for Windows-safe atomic IO helpers used by PromotedStore and audit JSONL."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from chaosgen.storage.atomic_io import (
    ExclusiveFileLock,
    append_jsonl_line,
    iter_jsonl,
    read_text_with_retry,
    replace_with_retry,
)


def test_replace_with_retry(tmp_path: Path):
    src = tmp_path / "a.tmp"
    dst = tmp_path / "a.json"
    src.write_text('{"ok": true}', encoding="utf-8")
    replace_with_retry(src, dst)
    assert json.loads(dst.read_text(encoding="utf-8"))["ok"] is True
    assert not src.exists()


def test_replace_with_retry_survives_transient_sharing_violation(tmp_path: Path, monkeypatch):
    """Windows hands out WinError 5/32 while a reader still holds the handle."""
    src = tmp_path / "a.tmp"
    dst = tmp_path / "a.json"
    src.write_text('{"ok": true}', encoding="utf-8")

    calls = {"n": 0}
    real_replace = os.replace

    def flaky_replace(a, b):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(13, "Access is denied")
        real_replace(a, b)

    monkeypatch.setattr(os, "replace", flaky_replace)
    replace_with_retry(src, dst, base_delay=0.001)

    assert calls["n"] == 3
    assert dst.is_file()


def test_replace_with_retry_raises_after_exhausting_attempts(tmp_path: Path, monkeypatch):
    src = tmp_path / "a.tmp"
    src.write_text("{}", encoding="utf-8")

    def always_busy(a, b):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(os, "replace", always_busy)
    with pytest.raises(PermissionError):
        replace_with_retry(src, tmp_path / "a.json", attempts=3, base_delay=0.001)


def test_replace_with_retry_reraises_unexpected_oserror(tmp_path: Path, monkeypatch):
    """A missing source is a bug, not contention — fail fast instead of looping."""
    src = tmp_path / "a.tmp"
    src.write_text("{}", encoding="utf-8")

    def not_found(a, b):
        raise FileNotFoundError(2, "No such file")

    monkeypatch.setattr(os, "replace", not_found)
    with pytest.raises(FileNotFoundError):
        replace_with_retry(src, tmp_path / "a.json", base_delay=0.001)


def test_read_text_with_retry_survives_sharing_violation(tmp_path: Path, monkeypatch):
    """A file busy right after os.replace is not a corrupt file."""
    target = tmp_path / "store.json"
    target.write_text('{"ok": true}', encoding="utf-8")

    calls = {"n": 0}
    real_read = Path.read_text

    def flaky_read(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(13, "Access is denied")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read)
    assert json.loads(read_text_with_retry(target, base_delay=0.001))["ok"] is True
    assert calls["n"] == 3


def test_read_text_with_retry_propagates_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        read_text_with_retry(tmp_path / "absent.json", base_delay=0.001)


def test_read_text_with_retry_gives_up_with_original_error(tmp_path: Path, monkeypatch):
    target = tmp_path / "store.json"
    target.write_text("{}", encoding="utf-8")

    def always_denied(self, *args, **kwargs):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "read_text", always_denied)
    with pytest.raises(PermissionError):
        read_text_with_retry(target, attempts=3, base_delay=0.001)


def test_exclusive_lock_never_proceeds_unlocked(tmp_path: Path):
    lock_path = tmp_path / "store.lock"
    with ExclusiveFileLock(lock_path):
        with pytest.raises(TimeoutError):
            with ExclusiveFileLock(lock_path, timeout=0.1, poll=0.01):
                pass
    assert not lock_path.exists()


def test_exclusive_lock_released_on_exception(tmp_path: Path):
    lock_path = tmp_path / "store.lock"
    with pytest.raises(RuntimeError):
        with ExclusiveFileLock(lock_path):
            raise RuntimeError("writer blew up")
    assert not lock_path.exists()

    with ExclusiveFileLock(lock_path, timeout=0.1):
        pass


def test_append_jsonl_parallel_no_lost_lines(tmp_path: Path):
    path = tmp_path / "audit_events.jsonl"
    total = 40
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            # Generous lock budget: the assertion under test is data integrity,
            # not how fast a loaded CI box can fsync 40 times.
            append_jsonl_line(
                path, {"event_id": str(i), "n": i}, lock_timeout=120.0
            )
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(total)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"writers failed: {errors!r}"
    rows = list(iter_jsonl(path))
    assert len(rows) == total
    assert {row["n"] for row in rows} == set(range(total))


def test_iter_jsonl_skips_truncated_tail(tmp_path: Path, caplog):
    """A9: a crash mid-write must not hide the events already fsynced."""
    path = tmp_path / "audit_events.jsonl"
    append_jsonl_line(path, {"event_id": "1", "n": 1})
    append_jsonl_line(path, {"event_id": "2", "n": 2})
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"event_id": "3", "n": 3')  # torn last line, no newline

    with caplog.at_level("WARNING"):
        rows = list(iter_jsonl(path))

    assert [row["n"] for row in rows] == [1, 2]
    assert any("corrupt JSONL line" in rec.message for rec in caplog.records)


def test_iter_jsonl_skips_blank_and_non_object_lines(tmp_path: Path):
    path = tmp_path / "audit_events.jsonl"
    path.write_text(
        '{"n": 1}\n\n  \n"just-a-string"\n[1, 2]\n{"n": 2}\n', encoding="utf-8"
    )
    assert [row["n"] for row in iter_jsonl(path)] == [1, 2]


def test_iter_jsonl_missing_file_yields_nothing(tmp_path: Path):
    assert list(iter_jsonl(tmp_path / "absent.jsonl")) == []
