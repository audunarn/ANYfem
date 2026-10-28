"""Bounded spooling of serialized worker messages (sender and receiver).

Pure in-process tests of the spool, the sender's chunked read-back and the
receiver's spooled reassembly; the real worker flow is exercised in
``test_result_transport.py``.  Messages are synthetic bytes, no solve runs.
"""

from __future__ import annotations

import tempfile

import numpy as np
import pytest

import anyfem.job_process as transport
from anyfem._transport_spool import (
    SPOOL_MEMORY_BYTES,
    BoundedSpool,
    TransportSpoolError,
)
from anyfem.result_transport import ResultTransportError, encode_result_stream

MIB = 1024 * 1024


class Frames:
    """A pipe stand-in that records frames."""

    def __init__(self):
        self.frames = []

    def send_bytes(self, data):
        assert len(data) <= transport._PIPE_FRAME_BYTES
        self.frames.append(bytes(data))


def receive(frames):
    incoming = transport._IncomingMessage()
    value = None
    for frame in frames:
        value = incoming.accept(frame)
    return incoming, value


# ----------------------------------------------------------------------
# the spool itself
# ----------------------------------------------------------------------
def test_default_memory_threshold_is_8_mib():
    assert SPOOL_MEMORY_BYTES == 8 * MIB
    assert transport._SPOOL_MEMORY_BYTES == 8 * MIB


def test_small_message_stays_in_memory_and_reads_back():
    spool = BoundedSpool(limit=10 * MIB)
    spool.write(b"abc")
    spool.write(memoryview(b"def"))
    assert spool.size == 6 and not spool.rolled_to_disk
    assert spool.read_all() == b"abcdef"
    spool.close()


def test_rollover_precedes_a_write_that_crosses_the_threshold():
    spool = BoundedSpool(limit=100 * MIB, memory_bytes=1024)
    events = []
    real_rollover, real_write = spool._file.rollover, spool._file.write

    def rollover():
        events.append("rollover")
        real_rollover()

    def write(data):
        events.append(("write", spool.rolled_to_disk))
        return real_write(data)

    spool._file.rollover = rollover
    spool._file.write = write
    spool.write(b"a" * 512)
    spool.write(b"b" * 4096)  # crosses 1024: must roll BEFORE buffering it
    assert events == [("write", False), "rollover", ("write", True)]
    assert spool.rolled_to_disk
    spool.rewind()
    assert spool.read(5000) == b"a" * 512 + b"b" * 4096
    spool.close()


def test_a_single_large_write_never_sits_in_memory_first():
    spool = BoundedSpool(limit=100 * MIB, memory_bytes=1024)
    spool.write(b"z" * (2 * MIB))
    assert spool.rolled_to_disk
    assert spool.hexdigest()
    spool.close()


def test_size_limit_is_enforced_before_any_write():
    spool = BoundedSpool(limit=10, memory_bytes=4)
    spool.write(b"12345")
    with pytest.raises(TransportSpoolError, match="exceeds 10-byte"):
        spool.write(b"123456")
    assert spool.size == 5
    spool.close()


def test_cancellation_stops_serialization_at_the_next_write():
    state = {"cancel": False}
    spool = BoundedSpool(limit=MIB, cancelled=lambda: state["cancel"])
    spool.write(b"ok")
    state["cancel"] = True
    with pytest.raises(TransportSpoolError, match="cancelled"):
        spool.write(b"more")
    assert spool.size == 2
    spool.close()


def test_a_disk_failure_is_a_spool_error_and_cleanup_still_works():
    spool = BoundedSpool(limit=MIB, memory_bytes=8)

    def broken(data):
        raise OSError("disk full")

    spool._file.write = broken
    with pytest.raises(TransportSpoolError, match="disk full"):
        spool.write(b"x" * 64)
    spool.close()
    spool.close()  # idempotent
    assert spool.closed


def test_dumps_spooled_enforces_the_cap_while_serializing(monkeypatch):
    monkeypatch.setattr(transport, "_MAX_TRANSFER_BYTES", 1000)
    with pytest.raises(TransportSpoolError):
        transport.dumps_spooled(("result", np.zeros(10_000)))


def test_dumps_spooled_cancellation_and_pickle_refusal_are_distinct():
    with pytest.raises(TransportSpoolError):
        transport.dumps_spooled(("result", b"x" * 100), cancelled=lambda: True)

    class Refuses:
        def __reduce_ex__(self, protocol):
            raise RuntimeError("refused")

    with pytest.raises(RuntimeError, match="refused"):
        transport.dumps_spooled(("result", Refuses()))


def test_codec_stream_does_not_turn_spool_errors_into_codec_refusals():
    import result_transport_helpers as helpers

    def serialize(envelope):
        raise TransportSpoolError("disk full")

    result = helpers.shared_model_batch(2)
    with pytest.raises(TransportSpoolError, match="disk full"):
        encode_result_stream(result, serialize)
    assert not issubclass(TransportSpoolError, ResultTransportError)


# ----------------------------------------------------------------------
# sender and receiver
# ----------------------------------------------------------------------
def test_large_spooled_message_round_trips_in_bounded_frames(monkeypatch):
    forbidden = []
    monkeypatch.setattr(
        BoundedSpool, "read_all", lambda self: forbidden.append("read_all") or b""
    )
    value = ("result", np.arange(2_000_000, dtype=float))  # ~16 MB: rolls to disk
    spool = transport.dumps_spooled(value)
    assert spool.rolled_to_disk
    connection = Frames()
    transport._send_serialized(connection, spool, "result")
    spool.close()
    assert not forbidden
    assert len(connection.frames) > 16
    incoming, decoded = receive(connection.frames)
    assert incoming.buffer is None
    assert decoded[0] == "result"
    assert np.array_equal(decoded[1], value[1])


def test_receiver_spools_instead_of_buffering_in_bytesio():
    value = ("result", b"q" * (3 * MIB))
    spool = transport.dumps_spooled(value)
    connection = Frames()
    transport._send_serialized(connection, spool, "result")
    spool.close()
    incoming = transport._IncomingMessage()
    assert incoming.accept(connection.frames[0]) is None
    assert isinstance(incoming.buffer, tempfile.SpooledTemporaryFile)
    incoming.close()
    assert incoming.buffer is None
    incoming.close()  # idempotent


def test_small_spooled_message_uses_the_single_frame_route():
    spool = transport.dumps_spooled(("result", 1))
    connection = Frames()
    transport._send_serialized(connection, spool, "result")
    spool.close()
    assert len(connection.frames) == 1 and connection.frames[0][:1] == b"C"
    assert receive(connection.frames)[1] == ("result", 1)


def test_digest_mismatch_and_partial_streams_publish_nothing():
    spool = transport.dumps_spooled(("result", b"w" * (2 * MIB)))
    connection = Frames()
    transport._send_serialized(connection, spool, "result")
    spool.close()
    frames = list(connection.frames)
    damaged = bytearray(frames[2])
    damaged[-1] ^= 0xFF
    frames[2] = bytes(damaged)
    with pytest.raises(transport.WorkerProcessError, match="digest mismatch"):
        receive(frames)
    incoming = transport._IncomingMessage()
    for frame in connection.frames[:-1]:
        assert incoming.accept(frame) is None
    assert incoming.buffer is not None
    incoming.close()
    assert incoming.buffer is None


def test_sender_still_reports_a_pipe_failure_after_the_header():
    class Failing(Frames):
        def send_bytes(self, data):
            if data[:1] == b"D":
                raise OSError("pipe broke")
            super().send_bytes(data)

    spool = transport.dumps_spooled(("result", b"p" * (2 * MIB)))
    connection = Failing()
    with pytest.raises(transport.WorkerProcessError, match="pipe broke"):
        transport._send_serialized(connection, spool, "result")
    spool.close()
    assert connection.frames[-1][:1] == b"A"
