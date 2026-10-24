"""Standalone native archive viewer; GUI dependencies load only on demand."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..post.archive import PreparedArchive


def show_archive(args: Any, prepared: PreparedArchive) -> int:
    """Attach a prepared archive to the standalone Tk viewer."""
    from ..post.archive import ArchiveSession

    import tkinter as tk
    from tkinter import ttk
    from any3dview.gpu import Any3DView

    root = tk.Tk()
    root.title(f"ANYfem archive — {args.archive.name}")
    root.geometry("1000x720")
    toolbar = ttk.Frame(root)
    toolbar.pack(fill="x")
    viewer = Any3DView(root, width=1000, height=650)
    viewer.pack(fill="both", expand=True)
    status = tk.StringVar(value="Select a case and component")
    ttk.Label(root, textvariable=status).pack(fill="x")
    session: ArchiveSession | None = None
    try:
        def show_presented(record: Any) -> None:
            status.set(f"Displayed: {record.case_id} — {record.quantity_id} / {record.component}")

        session = ArchiveSession(prepared, viewer, expected_model_identity=args.model_identity, expected_mesh_identity=args.mesh_identity, on_presented=show_presented)
        # Failed/uncertified cases remain discoverable through --list, but cannot
        # be presented as engineering fields. The reader enforces this again.
        cases = [row for row in session.case_summaries() if row.get("certified") is True and row.get("status") in {"completed", "complete", "converged", "success", "succeeded", "ok"}]
        components = [row for row in session.components() if row.get("availability") != "unavailable"]
        if not cases or not components:
            raise ValueError("archive has no completed certified case/component to display")
        case_ids = [str(row["case_id"]) for row in cases]
        labels = [f'{row["quantity_id"]} / {row["component"]} [{row["unit"]}] ({row["basis"]}, {row["sampling"]})' for row in components]
        case_choice = ttk.Combobox(toolbar, state="readonly", values=case_ids, width=28)
        field_choice = ttk.Combobox(toolbar, state="readonly", values=labels, width=44)
        sample_label = ttk.Label(toolbar, text="Native sample:")
        sample_choice = ttk.Combobox(toolbar, state="readonly", width=16)
        case_choice.pack(side="left", padx=4, pady=4)
        field_choice.pack(side="left", padx=4, pady=4)
        sample_label.pack(side="left", padx=4)
        sample_choice.pack(side="left", padx=4, pady=4)
        case_choice.current(case_ids.index(args.case_id) if args.case_id is not None else 0)
        field_index = next((i for i, row in enumerate(components) if row["quantity_id"] == args.quantity and row["component"] == args.component), None)
        if (args.quantity is None) != (args.component is None):
            raise ValueError("--quantity and --component must be supplied together")
        if args.quantity is not None and field_index is None:
            raise ValueError("requested component is unavailable")
        field_choice.current(field_index if field_index is not None else 0)
        selection = [int(value) for value in args.indices.split(",")] if args.indices is not None else None
        if args.indices is not None and args.sample is not None:
            raise ValueError("--indices and --sample are alternative explicit selections")
        sample_component = None

        def request(*_: Any) -> None:
            nonlocal sample_component
            row = components[field_choice.current()]
            try:
                selected_rows = selection
                if selected_rows is None:
                    key = (row["quantity_id"], row["component"])
                    if key != sample_component:
                        ordinal = args.sample if sample_component is None and args.sample is not None else 1
                        if ordinal < 1:
                            raise ValueError("native sample ordinal must be positive")
                        # The worker discovers the actual range; this is the
                        # explicit requested ordinal, not an invented count.
                        sample_choice.configure(state="readonly", values=("All samples", f"Sample {ordinal}"))
                        sample_choice.set(f"Sample {ordinal}")
                        sample_component = key
                    chosen = sample_choice.get()
                    if chosen == "All samples":
                        serial = session.request_component(case_choice.get(), *key)
                    else:
                        ordinal = int(chosen.removeprefix("Sample "))

                        def sample_options(maximum: int) -> None:
                            if key != sample_component:
                                return
                            if maximum * 128 > session.reader.working_bytes:
                                sample_choice.configure(state="disabled", values=("Range too large",))
                                sample_choice.set("Range too large")
                                status.set("Native sample range exceeds menu budget; use explicit --indices")
                                return
                            current = sample_choice.get()
                            choices = ["All samples"] + [f"Sample {index}" for index in range(1, maximum + 1)]
                            sample_choice.configure(values=choices)
                            sample_choice.set(current if current in choices else "Choose native sample")

                        serial = session.request_native_sample(case_choice.get(), *key, ordinal, on_samples_ready=sample_options)
                else:
                    sample_choice.configure(state="disabled", values=("Explicit indices",))
                    sample_choice.set("Explicit indices")
                    serial = session.request_component(case_choice.get(), row["quantity_id"], row["component"], selected_rows)
                status.set(f"Requested {serial}: {case_choice.get()} — {labels[field_choice.current()]}")
            except Exception as error:
                status.set(f"Refused: {error}")

        def poll_error() -> None:
            error = getattr(session.view, "error", None)
            if error is not None:
                status.set(f"Refused: {error}")
            if not session._closed:
                root.after(100, poll_error)

        case_choice.bind("<<ComboboxSelected>>", request)
        field_choice.bind("<<ComboboxSelected>>", request)
        sample_choice.bind("<<ComboboxSelected>>", request)
        viewer.fit_to_scene()
        request()
        poll_error()

        def close() -> None:
            session.close()
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", close)
        root.mainloop()
    finally:
        if session is not None:
            session.close()
        try:
            root.destroy()
        except tk.TclError:
            pass
    return 0
