from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

_POSIX_PERMISSIONS = os.name == "posix"


def _chmod(path: Path, mode: int) -> None:
    if not _POSIX_PERMISSIONS:
        return
    try:
        path.chmod(mode)
    except OSError:
        pass


def ensure_private_directory(path: Path, *, tighten_existing: bool = False) -> Path:
    """Create an application directory privately and tighten owned directories on POSIX."""

    existed = path.exists()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not existed or tighten_existing:
        _chmod(path, 0o700)
    return path


def ensure_private_file(path: Path) -> Path:
    """Best effort POSIX owner-only permissions for an existing file."""

    if path.exists():
        _chmod(path, 0o600)
    return path


def atomic_write_private_text(path: Path, content: str) -> None:
    ensure_private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        ensure_private_file(temporary)
        os.replace(temporary, path)
        ensure_private_file(path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def private_text_writer(path: Path) -> Iterator[TextIO]:
    """Open a potentially sensitive text export with owner-only POSIX permissions."""

    ensure_private_directory(path.parent)
    ensure_private_file(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    ensure_private_file(path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            yield stream
    finally:
        ensure_private_file(path)
