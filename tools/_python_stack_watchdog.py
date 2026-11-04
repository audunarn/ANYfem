"""Bounded, passive Python-frame diagnostics; never a deadline or crash handler.

The Python worker needs the GIL: native code retaining it can delay snapshots.
Only strong-reference frame/code metadata is read, never locals or source text.
"""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import threading
import time


class PassiveWatchdog:
    def __init__(self, directory, *, interval=60., max_frames=32, max_threads=8, max_bytes=262144):
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError('snapshot interval must be positive and finite')
        if min(max_frames, max_threads) < 1 or max_bytes < 1024:
            raise ValueError('snapshot bounds are too small')
        self.directory = Path(directory)
        self.path = self.directory / 'python-stack-snapshots.jsonl'
        self.path.open('x', encoding='utf-8').close()
        self.interval, self.max_frames, self.max_threads = interval, max_frames, max_threads
        self.max_bytes, self.bytes, self.snapshots = max_bytes, 0, 0
        self._condition = threading.Condition()
        self._thread = None
        self._active = None
        self._stop = self._closed = self._exhausted = False
        self._error = None
        self._status_error = self._retirement_error = None
        self._error_truncated = self._status_error_truncated = self._retirement_error_truncated = False
        self._retirement_verified = True
        self._status('idle', True)

    @property
    def failed(self):
        return self._error is not None

    def check(self):
        if self._error is not None:
            detail = [self._error, 'retirement_verified=' + str(self._retirement_verified)]
            if self._retirement_error is not None:
                detail.append('retirement_error=' + self._retirement_error)
            if self._status_error is not None:
                detail.append('status_error=' + self._status_error)
            raise RuntimeError('passive snapshot infrastructure failed: ' + '; '.join(detail))

    def _remember_error(self, field, error):
        name, message = type(error).__name__, str(error)
        setattr(self, field, name[:128] + ': ' + message[:512])
        setattr(self, field + '_truncated', len(name) > 128 or len(message) > 512)

    def _status(self, status, retired):
        self._retirement_verified = retired
        target = self.directory / 'python-stack-watchdog-status.json'
        temporary = target.with_suffix('.' + str(threading.get_ident()) + '.tmp')
        if self.failed:
            status = 'failed'
        temporary.write_text(json.dumps(dict(status=status, retirement_verified=retired,
            thread_started=(False if self._thread is None else
                            True if getattr(self._thread, 'ident', None) is not None else None),
            snapshots=self.snapshots, bytes=self.bytes,
            truncated=self._exhausted, error=self._error,
            status_error=self._status_error, retirement_error=self._retirement_error,
            error_truncated=self._error_truncated, status_error_truncated=self._status_error_truncated,
            retirement_error_truncated=self._retirement_error_truncated,
            limits=dict(max_threads=self.max_threads, max_frames=self.max_frames, max_bytes=self.max_bytes),
            limitations=['requires GIL scheduling; native GIL-held hangs may prevent snapshots',
                         'bounded thread/frame metadata only; no locals or source captured'])), encoding='utf-8')
        temporary.replace(target)

    def arm(self, nodeid, phase):
        try:
            with self._condition:
                self.check()
                if self._closed:
                    raise RuntimeError('passive snapshot observer already closed')
                self._active = (str(nodeid)[:1024], phase, len(str(nodeid)) > 1024)
                self._due = time.monotonic() + self.interval
                if self._thread is None:
                    self._thread = threading.Thread(target=self._run, name='anyfem-python-snapshot', daemon=True)
                    self._thread.start()
                self._condition.notify_all()
            self._status('running', False)
        except BaseException as error:
            # start() may have launched the OS thread before interruption.
            # Preserve its only handle and the original exception; never retry.
            with self._condition:
                self._stop = True
                self._active = None
                self._condition.notify_all()
                if self._error is None:
                    self._remember_error('_error', error)
            try:
                self._status('failed', self._thread is None)
            except BaseException as status_error:
                self._remember_error('_status_error', status_error)
            raise

    def phase(self, nodeid, phase):
        with self._condition:
            self.check()
            if self._active is not None:
                if self._active[0] != str(nodeid)[:1024]:
                    raise RuntimeError('passive snapshot node changed without disarm')
                self._active = (self._active[0], phase, self._active[2])

    def disarm(self):
        with self._condition:
            self._active = None
            self._condition.notify_all()
            self.check()

    def _capture(self):
        frames = sys._current_frames()
        result, truncated = [], len(frames) > self.max_threads
        frame = code = parent = None
        try:
            selected = sorted(frames)
            main = threading.main_thread().ident
            if main in frames:
                selected.remove(main)
                selected.insert(0, main)
            selected = selected[:self.max_threads]
            for ident in list(frames):
                if ident not in selected:
                    del frames[ident]
            for ident in selected:
                frame = frames.pop(ident)
                stack = []
                while frame is not None and len(stack) < self.max_frames:
                    code = frame.f_code
                    filename, function = code.co_filename, code.co_name
                    truncated |= len(filename) > 256 or len(function) > 128
                    stack.append(dict(filename=filename[:256], function=function[:128], line=frame.f_lineno))
                    parent = frame.f_back
                    frame = parent
                    code = None
                truncated |= frame is not None
                result.append(dict(thread_id=ident, frames=stack))
        finally:
            frames.clear()
            frame = code = parent = None
        return result, bool(truncated)

    def _snapshot(self):
        threads, truncated = self._capture()
        record = dict(nodeid=self._active[0], phase=self._active[1],
            utc=datetime.now(timezone.utc).isoformat(), monotonic_ns=time.monotonic_ns(),
            threads=threads, nodeid_truncated=self._active[2], truncated=truncated or self._active[2])
        # Reserve room for an explicit terminal file-budget record.
        room = min(65536, self.max_bytes - self.bytes - 256)
        data = (json.dumps(record, ensure_ascii=True) + '\n').encode('utf-8')
        while len(data) > room and threads:
            record['truncated'] = True
            if threads[-1]['frames']:
                threads[-1]['frames'].pop()
            else:
                threads.pop()
            data = (json.dumps(record, ensure_ascii=True) + '\n').encode('utf-8')
        if len(data) > room or not threads:
            data = b'{"event":"snapshot_file_budget_exhausted","truncated":true}\n'
            self._exhausted = True
        return data

    def _publish(self, data):
        with self.path.open('ab') as stream:
            stream.write(data)
            stream.flush()
        self.bytes += len(data)
        self.snapshots += 1
        self._status('running', False)

    def _run(self):
        try:
            while True:
                with self._condition:
                    while not self._stop:
                        if self._active is None or self._exhausted:
                            self._condition.wait()
                            continue
                        remaining = self._due - time.monotonic()
                        if remaining > 0:
                            self._condition.wait(remaining)
                            continue
                        data = self._snapshot()
                        self._due = time.monotonic() + self.interval
                        break
                    if self._stop:
                        return
                # Only encoded metadata survives capture. Never hold the phase
                # lock or strong frame/code references during file I/O.
                self._publish(data)
        except BaseException as error:
            with self._condition:
                if self._error is None:
                    self._remember_error('_error', error)
                self._stop = True
                self._condition.notify_all()
            try:
                self._status('failed', False)
            except OSError as status_error:
                self._remember_error('_status_error', status_error)

    def close(self):
        self._stop = True
        locked = self._condition.acquire(timeout=2)
        if locked:
            try:
                self._active = None
                self._condition.notify_all()
            finally:
                self._condition.release()
        thread = self._thread
        joined = True
        if thread is not None:
            try:
                thread.join(timeout=2)
            except RuntimeError as error:
                # A start interruption before the public started marker is
                # uncertain, even when is_alive() presently reports False.
                joined = False
                self._remember_error('_retirement_error', error)
        retired = thread is None or (joined and not thread.is_alive())
        self._retirement_verified = retired
        if not retired:
            if self._retirement_error is None:
                self._retirement_error = 'could not retire passive snapshot thread'
            if self._error is None:
                self._error = 'could not retire passive snapshot thread'
        self._closed = True
        try:
            self._status('failed' if self.failed else 'closed', retired)
        except Exception as status_error:
            self._remember_error('_status_error', status_error)
            if self._error is None:
                self._remember_error('_error', status_error)
            try:
                self.check()
            except RuntimeError as combined:
                raise combined from status_error
        except BaseException as interruption:
            self._remember_error('_status_error', interruption)
            if self._error is None:
                self._remember_error('_error', interruption)
            raise
        self.check()
