"""Opt-in pytest evidence only: no test selection, outcome or timeout policy."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

import pytest

from tools._python_stack_watchdog import PassiveWatchdog


class _Journal:
    def __init__(self, config):
        self.config = config
        value = os.environ.get("ANYFEM_CI_DIAGNOSTICS_DIR")
        if not value:
            raise pytest.UsageError("ANYfem diagnostics require an owned output directory")
        self.directory = Path(value).resolve()
        if not self.directory.is_dir():
            raise pytest.UsageError("ANYfem diagnostics output directory is absent")
        self.path = self.directory / "pytest-progress.jsonl"
        self.path.open("x", encoding="utf-8").close()
        self.sequence = 0

    def live(self, text):
        capture = self.config.pluginmanager.get_plugin("capturemanager")
        if capture is None:
            print(text, flush=True)
        else:
            # Only our diagnostic write escapes capture; test/fixture code does not.
            with capture.global_and_fixture_disabled():
                print(text, flush=True)

    def write(self, event, *, nodeid=None, phase=None, live=False, **fields):
        self.sequence += 1
        row = {"sequence": self.sequence, "event": event, "nodeid": nodeid,
               "phase": phase, "utc": datetime.now(timezone.utc).isoformat(),
               "monotonic_ns": time.monotonic_ns(), **fields}
        text = json.dumps(row, ensure_ascii=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(text + "\n")
            stream.flush()
        current = self.directory / "pytest-current.json"
        temporary = current.with_suffix(".json.tmp")
        temporary.write_text(text + "\n", encoding="utf-8")
        # Windows readers may briefly open the live snapshot without delete
        # sharing. The append-only journal above remains the primary evidence.
        for attempt in range(26):
            try:
                temporary.replace(current)
                break
            except PermissionError as error:
                if attempt == 25:
                    with self.path.open('a',encoding='utf8') as stream:
                        stream.write(json.dumps(dict(event='current_snapshot_error',sequence=self.sequence,error=str(error)))+'\n')
                    break
                time.sleep(.01)
        if live:
            compact = {key: value for key, value in row.items()
                       if key not in {"longrepr", "captured_sections"}}
            self.live("\n[anyfem-ci] " + json.dumps(compact, ensure_ascii=True))

    def failure(self, report, phase):
        detail = str(report.longrepr)
        sections = list(getattr(report, "sections", ()))
        self.write("failure", nodeid=report.nodeid, phase=phase, live=True,
                   longrepr=detail, captured_sections=sections)
        text = "[anyfem-ci failure] " + report.nodeid + " (" + phase + ")\n" + detail
        for title, content in sections:
            text += "\n--- " + title + " ---\n" + content
        self.live(text)


class _Progress:
    def __init__(self, config):
        self.journal = _Journal(config)
        self.watchdog = PassiveWatchdog(self.journal.directory,
            interval=float(config.getini("anyfem_snapshot_timeout")))
        config.add_cleanup(self.watchdog.close)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item, nextitem):
        self.watchdog.arm(item.nodeid, "setup")
        try:
            return (yield)
        finally:
            self.watchdog.disarm()

    def pytest_sessionstart(self, session):
        self.journal.write("session_start", phase="collection", live=True)

    def pytest_collectstart(self, collector):
        self.journal.write("collection_start", nodeid=collector.nodeid,
                           phase="collection", live=True)

    def pytest_collectreport(self, report):
        if report.failed:
            self.journal.failure(report, "collection")

    def pytest_collection_finish(self, session):
        nodes = [item.nodeid for item in session.items]
        (self.journal.directory / "pytest-collection.json").write_text(
            json.dumps({"nodes": nodes, "collection_errors": session.testsfailed}, indent=2),
            encoding="utf-8")
        self.journal.write("collection_finish", phase="collection", live=True,
                           collected_count=len(nodes), collection_errors=session.testsfailed)

    def pytest_runtest_logstart(self, nodeid, location):
        self.journal.write("node_start", nodeid=nodeid, phase="setup", live=True)

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_setup(self, item):
        self.watchdog.phase(item.nodeid, "setup")
        self.journal.write("phase_start", nodeid=item.nodeid, phase="setup", live=True)

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_call(self, item):
        self.watchdog.phase(item.nodeid, "call")
        self.journal.write("phase_start", nodeid=item.nodeid, phase="call", live=True)

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_teardown(self, item, nextitem):
        self.watchdog.phase(item.nodeid, "teardown")
        self.journal.write("phase_start", nodeid=item.nodeid, phase="teardown", live=True)

    def pytest_runtest_logreport(self, report):
        self.journal.write("phase_end", nodeid=report.nodeid, phase=report.when,
                           outcome=report.outcome, duration=report.duration)
        if report.failed:
            self.journal.failure(report, report.when)
        self.watchdog.check()

    def pytest_sessionfinish(self, session, exitstatus):
        self.watchdog.close()
        self.journal.write("session_finish", phase="session", live=True,
                           exit_status=int(exitstatus), collected_count=session.testscollected)


def pytest_addoption(parser):
    parser.addini("anyfem_snapshot_timeout", "Passive Python-frame snapshot interval", default="60")


def pytest_configure(config):
    config.pluginmanager.register(_Progress(config), "anyfem-ci-progress")
