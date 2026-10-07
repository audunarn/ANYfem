"""Standalone GUI stand-in for the contained parent-death tests.

Run as a script (never imported by pytest): it starts a contained mesh-style
worker whose job creates a child process (the ANYmesh pool stand-in), reports
both pids through a report file, and then waits like an application would.

The test kills THIS process only, and asserts that the whole owned tree died
through containment alone:

* Windows: the kill-on-close job object takes the worker and the pool child
  with it when this process's job handle disappears;
* Linux: the worker's strictly checked parent-death signal kills the worker,
  and the cleanup guardian -- which watches this process's multiprocessing
  sentinel independently -- kills the owned process group (the pool child,
  itself and any remaining descendant).

The ``if __name__ == "__main__"`` guard is mandatory: a spawned worker
re-imports this file.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


def pool_child_job(*, project, progress, cancellation_token=None):
    """Create a pool stand-in child, report it, then keep meshing."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    progress(f"child {child.pid}")
    time.sleep(120)
    return {"child_pid": child.pid}


def main() -> None:
    from anyfem.document import DocumentSession
    from anyfem.job_process import run_in_worker_process
    from anyfem.jobs import _cancellation_token
    from anyfem.model.project import Project

    report = Path(sys.argv[1])
    project = Project("parent-death")
    snapshot = DocumentSession(project).snapshot()
    state: dict[str, int] = {}

    def on_process(process) -> None:
        state["outer"] = int(process.pid)

    def progress(value) -> None:
        text = str(getattr(value, "message", value))
        if text.startswith("child "):
            state["child"] = int(text.split()[1])
        if "outer" in state and "child" in state:
            report.write_text(
                f"outer={state['outer']}\nchild={state['child']}\n", encoding="utf-8"
            )

    def run() -> None:
        run_in_worker_process(
            function=pool_child_job,
            kwargs={},
            snapshot=snapshot,
            project_override=None,
            progress=progress,
            token=_cancellation_token(),
            containment=True,
            on_process=on_process,
        )

    threading.Thread(target=run, name="contained-run", daemon=True).start()
    deadline = time.monotonic() + 30
    while not report.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(3600)  # the application keeps running until the test kills it


if __name__ == "__main__":
    main()
