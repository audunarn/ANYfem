"""Compact trusted-Python editor and console for the Details workspace."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..scripting import (
    ScriptConflictError,
    ScriptError,
    ScriptRunner,
)

__all__ = ["ScriptingPanel"]


class ScriptingPanel(ttk.Frame):
    """Run trusted local Python on an isolated working copy.

    The panel never commits from a worker callback.  It polls the future using
    ``after`` and performs the single transaction on Tk's owning thread.
    """

    title = "Scripting"
    POLL_MS = 50

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master, padding=8)
        self.app = app
        self._runner: ScriptRunner | None = None
        self._task = None
        self._poll_after = None

        ttk.Label(
            self,
            text="Trusted Python",
            font=("TkDefaultFont", 10, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            self,
            text=(
                "Runs on a working copy. Successful changes are validated and "
                "applied as one undo item. This is trusted local code, not an "
                "OS sandbox.\n"
                "Globals: project, selection, commands, meshes, analyses, "
                "jobs, np and checkpoint."
            ),
            foreground="#555555",
            wraplength=330,
            justify="left",
        ).pack(fill="x", pady=(2, 6))

        editor_frame = ttk.Frame(self)
        editor_frame.pack(fill="both", expand=True)
        self.source = tk.Text(
            editor_frame,
            height=16,
            width=46,
            wrap="none",
            undo=True,
            font="TkFixedFont",
        )
        source_y = ttk.Scrollbar(
            editor_frame, orient="vertical", command=self.source.yview
        )
        source_x = ttk.Scrollbar(
            editor_frame, orient="horizontal", command=self.source.xview
        )
        self.source.configure(yscrollcommand=source_y.set, xscrollcommand=source_x.set)
        self.source.grid(row=0, column=0, sticky="nsew")
        source_y.grid(row=0, column=1, sticky="ns")
        source_x.grid(row=1, column=0, sticky="ew")
        editor_frame.rowconfigure(0, weight=1)
        editor_frame.columnconfigure(0, weight=1)
        self.source.insert(
            "1.0",
            "# Trusted local Python; Ctrl+Enter runs atomically.\n"
            "print(project.name)\n"
            "# point = commands.run(commands.AddPoint(0, 0, 0))\n",
        )
        self.source.bind("<Control-Return>", self._run_shortcut)

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", pady=(6, 4))
        self._run_button = ttk.Button(buttons, text="Run", command=self._run)
        self._run_button.pack(side="left")
        self._cancel_button = ttk.Button(
            buttons, text="Cancel", command=self._cancel, state="disabled"
        )
        self._cancel_button.pack(side="left", padx=4)
        ttk.Button(buttons, text="Clear output", command=self.clear_output).pack(
            side="right"
        )

        ttk.Label(self, text="Console", font=("TkDefaultFont", 9, "bold")).pack(
            anchor="w"
        )
        self.output = tk.Text(
            self,
            height=9,
            width=46,
            wrap="word",
            state="disabled",
            font="TkFixedFont",
        )
        self.output.pack(fill="both", expand=True, pady=(2, 0))

    def refresh(self) -> None:
        """Follow document replacement without ever committing to an old one."""

        if (
            self._runner is not None
            and self._task is None
            and self._runner.session is not self.app.session
        ):
            self._runner.shutdown(wait=False)
            self._runner = None

    def clear_output(self) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

    def _write(self, text: str) -> None:
        if not text:
            return
        self.output.configure(state="normal")
        self.output.insert("end", text)
        self.output.see("end")
        self.output.configure(state="disabled")

    def _run_shortcut(self, _event=None) -> str:
        self._run()
        return "break"

    def _run(self) -> None:
        if self._task is not None:
            self.app.set_status("a script is already running")
            return
        source = self.source.get("1.0", "end-1c")
        if not source.strip():
            self.app.set_status("enter Python source before running")
            return
        if self._runner is None or self._runner.session is not self.app.session:
            if self._runner is not None:
                self._runner.shutdown(wait=False)
            self._runner = ScriptRunner(self.app.session)
        self._write("\n>>> Run working copy\n")
        try:
            self._task = self._runner.submit(source)
        except (ValueError, TypeError, PermissionError, RuntimeError) as error:
            self._write(f"{type(error).__name__}: {error}\n")
            self.app.set_status(str(error), error=True)
            self._task = None
            return
        self._run_button.configure(state="disabled")
        self._cancel_button.configure(state="normal")
        self.app.set_status("running script on a working copy")
        self._poll_after = self.after(self.POLL_MS, self._poll)

    def _cancel(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        self._cancel_button.configure(state="disabled")
        self.app.set_status("cancelling script")

    def _poll(self) -> None:
        self._poll_after = None
        task = self._task
        if task is None:
            return
        if not task.done():
            self._poll_after = self.after(self.POLL_MS, self._poll)
            return
        self._task = None
        self._run_button.configure(state="normal")
        self._cancel_button.configure(state="disabled")
        try:
            result = task.result()
            if self._runner is None or self._runner.session is not self.app.session:
                raise ScriptConflictError(
                    "the document was replaced while the script was running; "
                    "the proposal was discarded",
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            outcome = self._runner.commit(result)
        except ScriptError as error:
            self._write(error.stdout)
            self._write(error.stderr)
            if error.traceback_text:
                self._write(error.traceback_text)
            self._write(f"Rejected: {error}\n")
            self.app.set_status(str(error), error=True)
            return
        except (ValueError, TypeError, PermissionError, RuntimeError) as error:
            self._write(f"Rejected: {type(error).__name__}: {error}\n")
            self.app.set_status(str(error), error=True)
            return

        self._write(outcome.stdout)
        self._write(outcome.stderr)
        if outcome.return_value is not None:
            self._write(f"result = {outcome.return_value!r}\n")
        if outcome.committed:
            self._write("Applied as one undo item.\n")
            # Mesh-only scripts can intentionally replace the active cached
            # mesh. Model edits have already invalidated that cache.
            if outcome.meshes_changed:
                self.app.mesh = self.app.session.mesh_cache.get("active")
            self.app.set_status("script applied atomically")
        else:
            self._write("Completed; no document changes.\n")
            self.app.set_status("script completed")

    def destroy(self) -> None:
        if self._poll_after is not None:
            try:
                self.after_cancel(self._poll_after)
            except tk.TclError:
                pass
            self._poll_after = None
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._runner is not None:
            self._runner.shutdown(wait=False)
            self._runner = None
        super().destroy()
