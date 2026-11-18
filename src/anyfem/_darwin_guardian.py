"""Darwin containment guardian: a tiny fresh interpreter, standard library only.

Launched by a contained worker as ``python -I <this file> GROUP LIVENESS SENTINEL ACK``
with explicit ``pass_fds`` and ``close_fds``.  It deliberately does not import
the anyfem package (start-up cost, import side effects).

* GROUP: the worker's pid; the worker ``setsid``ed, so this is the owned group
  and the guardian (spawned without a new session) lives inside it.  The
  guardian proves that with ``os.getpgrp() == GROUP`` and refuses otherwise.
* LIVENESS: read end of a pipe whose only write end the worker holds; EOF
  means the worker died.
* SENTINEL: the worker's copy of the ORIGINAL multiprocessing parent sentinel
  (the application process), not this process's own parent; EOF means the
  application died.
* ACK: write end on which ``ready`` (or ``refused``) is sent once the watch is
  armed; the worker proceeds to its own ready/go handshake only after that.

When either watch fires, or the watch itself fails, the guardian kills its OWN
process group (``os.getpgrp()``, never a number from elsewhere), itself included.
"""

import os
import select
import signal
import sys

POLL_SECONDS = 0.1


def watch(liveness_fd, sentinel_fd, poll=POLL_SECONDS):
    """Block until a watched descriptor reaches EOF; return which one fired.

    Returns ``"outer"``, ``"parent"`` or ``"watch-failed"`` (any unexpected
    error; the caller treats all three as "kill the group").  Data on a
    descriptor is drained and ignored: neither carries a payload.
    """

    watched = {"outer": liveness_fd, "parent": sentinel_fd}
    while True:
        try:
            ready, _, _ = select.select(list(watched.values()), [], [], poll)
        except InterruptedError:
            continue
        except (OSError, ValueError):
            return "watch-failed"
        for name, fd in list(watched.items()):
            if fd not in ready:
                continue
            try:
                data = os.read(fd, 64)
            except InterruptedError:
                continue
            except OSError:
                return name
            if data == b"":
                return name


def _kill_own_group():
    try:
        os.killpg(os.getpgrp(), signal.SIGKILL)
    except OSError:
        pass


def main(argv):
    try:
        group, liveness_fd, sentinel_fd, ack_fd = (int(value) for value in argv)
    except ValueError:
        return 2
    verdict = b"ready" if (os.getpgrp() == group and os.getpid() != group) else b"refused"
    try:
        os.write(ack_fd, verdict)
        os.close(ack_fd)
    except OSError:
        return 3
    if verdict != b"ready":
        return 4
    watch(liveness_fd, sentinel_fd)
    _kill_own_group()
    return 0


if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.stdout.flush()
    os._exit(code)
