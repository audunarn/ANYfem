"""Bounded spool for serialized worker messages (sender and receiver).

A serialized result may be gigabytes.  The spool holds up to ``memory_bytes`` in
RAM and then rolls over to an anonymous temporary file, so neither side ever
needs the complete message as one ``bytes`` object.  The size cap is enforced
on every write -- while the message is being produced -- and an optional
``cancelled`` callable aborts serialization at the next write.
"""

from __future__ import annotations

import hashlib
import tempfile
from typing import Callable, Iterator

__all__ = ["SPOOL_MEMORY_BYTES", "TransportSpoolError", "BoundedSpool"]

SPOOL_MEMORY_BYTES = 8 * 1024 * 1024


class TransportSpoolError(OSError):
    """The spool refused a write: size cap, cancellation or disk failure.

    Distinct from a pickling refusal, which the direct-pickle fallback may
    answer with the plain-data codec; this error never triggers a fallback.
    ``cancelled`` is true only for an actual cancellation request, so callers
    report it as a cancellation rather than a transfer failure.
    """

    def __init__(self, message: str, *, cancelled: bool = False) -> None:
        super().__init__(message)
        self.cancelled = bool(cancelled)


class BoundedSpool:
    """Write-then-read spool with a running SHA-256 and a hard size cap."""

    def __init__(
        self,
        limit: int,
        memory_bytes: int = SPOOL_MEMORY_BYTES,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.limit = int(limit)
        self.size = 0
        self.memory_bytes = int(memory_bytes)
        self._cancelled = cancelled
        self._digest = hashlib.sha256()
        try:
            self._file = tempfile.SpooledTemporaryFile(max_size=int(memory_bytes))
        except OSError as error:  # pragma: no cover - temp directory unusable
            raise TransportSpoolError(f"cannot create transfer spool: {error}") from error
        self.closed = False

    # -- writing ---------------------------------------------------------
    def write(self, data) -> int:
        length = memoryview(data).nbytes
        if self._cancelled is not None and self._cancelled():
            raise TransportSpoolError("cancelled while serializing the result", cancelled=True)
        if self.size + length > self.limit:
            raise TransportSpoolError(
                f"serialized message exceeds {self.limit}-byte transfer limit"
            )
        try:
            # Roll to disk BEFORE a write that would cross the memory
            # threshold: SpooledTemporaryFile.write would otherwise buffer the
            # whole chunk in its BytesIO first and roll over afterwards.
            if not self.rolled_to_disk and self.size + length > self.memory_bytes:
                self._file.rollover()
            self._file.write(data)
        except OSError as error:
            raise TransportSpoolError(f"transfer spool write failed: {error}") from error
        self._digest.update(data)
        self.size += length
        return length

    def hexdigest(self) -> str:
        return self._digest.hexdigest()

    # -- reading ---------------------------------------------------------
    def rewind(self) -> None:
        self._file.seek(0)

    def read(self, size: int = -1) -> bytes:
        try:
            return self._file.read(size)
        except OSError as error:
            raise TransportSpoolError(f"transfer spool read failed: {error}") from error

    def read_all(self) -> bytes:
        self.rewind()
        return self.read(self.size)

    def chunks(self, chunk_size: int) -> Iterator[bytes]:
        self.rewind()
        remaining = self.size
        while remaining > 0:
            block = self.read(min(chunk_size, remaining))
            if not block:
                raise TransportSpoolError("transfer spool ended before its recorded size")
            remaining -= len(block)
            yield block

    @property
    def rolled_to_disk(self) -> bool:
        return bool(getattr(self._file, "_rolled", False))

    def file(self):
        """The underlying readable file (positioned by the caller)."""
        return self._file

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self._file.close()
            except Exception:  # noqa: BLE001 - teardown must never mask a result
                pass
