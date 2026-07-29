"""The ANYstructure parity ledger, and the migration gate.

ANYfem is meant to replace ANYstructure's ``fem_integration.py`` eventually,
but only once it is complete and verified.  Deciding when that is would be a
matter of opinion unless someone writes down what "complete" means, so this
module writes it down.

The ledger is deliberately unflattering.  ``fem_integration`` exposes 176
options; the entries below are grouped by capability and marked against what
ANYfem actually does today.  A capability is ``covered`` only when ANYfem can
do the same job, ``partial`` when it can do some of it, and ``missing``
otherwise.  Marking something covered because it is nearly covered would
defeat the point of keeping a ledger at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

__all__ = [
    "GATE_CRITERIA",
    "LEDGER",
    "ParityEntry",
    "gate_status",
    "parity_markdown",
    "parity_summary",
    "write_parity_report",
]

STATUSES = ("covered", "partial", "missing")

# The reference the ledger is written against.
SOURCE = "ANYstructure anystruct/fem_integration.py (RuntimeFEMOptions, 176 options)"


@dataclass(frozen=True)
class ParityEntry:
    """One capability of the GUI ANYfem intends to replace."""

    area: str
    capability: str
    status: str
    note: str = ""

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(
                f"{self.capability!r}: status must be one of {', '.join(STATUSES)}"
            )


LEDGER: Tuple[ParityEntry, ...] = (
    # -- modelling -----------------------------------------------------
    ParityEntry(
        "Modelling", "Parametric stiffened panel and cylinder geometry",
        "missing",
        "ANYfem models general geometry instead. The parametric front end is "
        "what ANYstructure would keep and call into, so this is a seam to "
        "build, not a gap to close.",
    ),
    ParityEntry(
        "Modelling", "General geometry (points, lines, arcs, plates, sweeps)",
        "covered", "Beyond what fem_integration can express at all.",
    ),
    ParityEntry(
        "Modelling", "Plate thickness regions", "covered",
        "A plate section per face; split a plate to vary it.",
    ),
    ParityEntry(
        "Modelling", "Stiffener and girder eccentricity", "covered",
        "A beam section carries an eccentricity; the mesher offsets its nodes "
        "along the plate normal and couples every station back to the plating "
        "with the solver's own MPC. Verified against a transformed section: "
        "ECC-01 puts the neutral axis within 0.2% and ECC-02 the stiffness "
        "within 1.5%.",
    ),
    ParityEntry(
        "Modelling", "Symmetry modelling", "missing",
        "No symmetry boundary generation.",
    ),
    # -- meshing -------------------------------------------------------
    ParityEntry(
        "Meshing", "Target element size and mesh fidelity", "covered",
        "Target size plus per-line division pinning.",
    ),
    ParityEntry(
        "Meshing", "Conformal mesh across shared boundaries", "covered",
        "Structural rather than tolerance based.",
    ),
    ParityEntry(
        "Meshing", "Local and point mesh refinement patches", "partial",
        "Per-line seeding overrides exist; graded refinement zones around a "
        "patch or a point do not.",
    ),
    ParityEntry(
        "Meshing", "8-node shells and 3-node beams (S8, B3)", "missing",
        "The mesher emits Q4 and 2-node beams only, though the solver "
        "supports more.",
    ),
    # -- loads and boundary conditions ---------------------------------
    ParityEntry(
        "Loads and BC", "Pressure, dead and follower", "covered", "",
    ),
    ParityEntry(
        "Loads and BC", "Edge and line loads with components", "covered", "",
    ),
    ParityEntry(
        "Loads and BC", "Point loads and moments", "covered", "",
    ),
    ParityEntry(
        "Loads and BC", "Surface traction", "covered",
        "Not in fem_integration; ANYfem adds it.",
    ),
    ParityEntry(
        "Loads and BC", "Acceleration fields and added mass", "covered", "",
    ),
    ParityEntry(
        "Loads and BC", "Enforced displacement", "covered", "",
    ),
    ParityEntry(
        "Loads and BC", "Supports, including per-DOF and per-edge", "covered", "",
    ),
    ParityEntry(
        "Loads and BC", "Load cases and factored combinations", "covered",
        "Not in fem_integration, which solves one case at a time.",
    ),
    ParityEntry(
        "Loads and BC", "Axial force, moment and shear resultants on a section",
        "missing",
        "fem_integration applies section resultants to a parametric panel; "
        "ANYfem has no equivalent shorthand.",
    ),
    # -- materials -----------------------------------------------------
    ParityEntry(
        "Materials", "DNV-RP-C208 steel grades and thickness classes",
        "covered", "Through the solver's own table.",
    ),
    ParityEntry(
        "Materials", "Nonlinear material with a hardening curve", "covered",
        "steel(..., nonlinear=True) attaches the solver's RP-C208 curve, and "
        "a nonlinear solve then yields. Plasticity is the layered-shell path: "
        "beams stay elastic, which is the solver's scope, not a wrapper "
        "limitation.",
    ),
    # -- analyses ------------------------------------------------------
    ParityEntry("Analyses", "Linear static", "covered", ""),
    ParityEntry("Analyses", "Modal", "covered", ""),
    ParityEntry(
        "Analyses", "Linear eigenvalue buckling", "covered",
        "Including shift, range and repeated-mode controls by passthrough.",
    ),
    ParityEntry("Analyses", "Nonlinear static", "covered", ""),
    ParityEntry(
        "Analyses", "Arc length and post-buckling", "covered", "",
    ),
    ParityEntry("Analyses", "Transient dynamics", "covered", ""),
    ParityEntry(
        "Analyses", "Rigid-sphere collision and impact", "partial",
        "The impact itself is covered: contact with an automatic penalty and "
        "time step, contact history, momentum balance, energy, damage and "
        "erosion. Adaptive mesh refinement around the impact zone, which "
        "fem_integration offers, is not -- it needs the same graded "
        "refinement the meshing entry is short of.",
    ),
    ParityEntry(
        "Analyses", "Fracture and element erosion", "covered",
        "Available in an impact and in a nonlinear static solve, through the "
        "solver's own damage and fracture configurations. Eroded element IDs "
        "come back on the result. Erosion is residual stiffness scaling after "
        "a converged increment -- an engineering screen, not crack mechanics, "
        "which is how the solver describes it too.",
    ),
    ParityEntry(
        "Analyses", "Capacity workflow (static to buckling to imperfect nonlinear)",
        "partial",
        "Every step exists separately and can be chained by script; the "
        "solver's packaged capacity_workflow is not wired up.",
    ),
    ParityEntry(
        "Analyses", "Geometric imperfections, standard and eigenmode",
        "covered", "",
    ),
    # -- results -------------------------------------------------------
    ParityEntry(
        "Results", "Displacement and stress contouring", "covered",
        "28 fields including derived surface stress.",
    ),
    ParityEntry("Results", "Probes and node/element readout", "covered", ""),
    ParityEntry(
        "Results", "Result along a path", "covered",
        "Not in fem_integration; ANYfem adds it.",
    ),
    ParityEntry(
        "Results", "Envelopes over steps or modes", "covered",
        "Not in fem_integration.",
    ),
    ParityEntry("Results", "Animation of steps and modes", "covered", ""),
    ParityEntry(
        "Results", "Time-history plots at a probe", "partial",
        "The data is available (node_history); there is no plot widget.",
    ),
    ParityEntry(
        "Results", "Stress reduction to panel buckling input", "missing",
        "fe_plate_fields reduces FE stresses into buckling-code input. That "
        "is ANYstructure domain logic and stays there.",
    ),
    ParityEntry(
        "Results", "Recovery policy controls (history mode, threads, memory)",
        "missing", "Solver options not surfaced by ANYfem.",
    ),
    # -- application ---------------------------------------------------
    ParityEntry("Application", "3D viewport with picking", "covered", ""),
    ParityEntry(
        "Application", "Loads and supports drawn on the model", "covered", "",
    ),
    ParityEntry(
        "Application", "Threaded solve with progress and cancel", "covered", "",
    ),
    ParityEntry(
        "Application", "Undo and redo", "covered",
        "Not in fem_integration.",
    ),
    ParityEntry(
        "Application", "Save and restore the analysis state", "partial",
        "ANYfem has its own project format; it cannot read "
        "fem_integration's save_runtime_fem_state files.",
    ),
    ParityEntry(
        "Application", "Report export", "covered",
        "Markdown and CSV.",
    ),
    # -- interop -------------------------------------------------------
    ParityEntry("Interop", "SESAM FEM import", "covered", ""),
    ParityEntry(
        "Interop", "SESAM SIF result import", "missing",
        "The solver reads SIF shell stresses; ANYfem does not.",
    ),
    ParityEntry(
        "Interop", "CalculiX deck export", "covered", "",
    ),
    ParityEntry(
        "Interop", "CalculiX FRD/INP result import", "missing",
        "fe_plate_fields reads FRD results; ANYfem does not.",
    ),
    ParityEntry(
        "Interop", "Handoff to the ANYstructure buckling session", "missing",
        "The migration itself, and the last thing to build.",
    ),
)


# ----------------------------------------------------------------------
# the gate
# ----------------------------------------------------------------------
GATE_CRITERIA: Tuple[str, ...] = (
    "every fem_integration analysis path reproduced within tolerance on a "
    "fixed set of ANYstructure models",
    "parity ledger has no missing or partial entries outside ANYstructure's "
    "own domain logic",
    "existing save_runtime_fem_state files importable",
    "headless API builds every ANYstructure model type without a GUI",
    "no performance regression on representative models",
)

# Entries that belong to ANYstructure rather than to ANYfem, and so do not
# block the migration.  Naming them explicitly keeps the gate honest: they are
# excluded on purpose, not by quietly leaving them out of the ledger.
OUT_OF_SCOPE: Tuple[str, ...] = (
    "Stress reduction to panel buckling input",
    "Handoff to the ANYstructure buckling session",
    "Parametric stiffened panel and cylinder geometry",
)


def parity_summary() -> Dict[str, Any]:
    """Counts by status, and by area."""

    counts = {status: 0 for status in STATUSES}
    by_area: Dict[str, Dict[str, int]] = {}
    for entry in LEDGER:
        counts[entry.status] += 1
        area = by_area.setdefault(entry.area, {status: 0 for status in STATUSES})
        area[entry.status] += 1
    return {
        "source": SOURCE,
        "total": len(LEDGER),
        "counts": counts,
        "by_area": by_area,
        "coverage": counts["covered"] / len(LEDGER) if LEDGER else 0.0,
    }


def gate_status() -> Dict[str, Any]:
    """Whether the ledger says the migration can proceed, and what blocks it.

    This answers only the ledger criterion.  The other gate criteria need a
    fixed model set, a state importer and a performance comparison that do not
    exist yet, so they are reported as outstanding rather than assumed met.
    """

    blocking = [
        entry
        for entry in LEDGER
        if entry.status in ("missing", "partial")
        and entry.capability not in OUT_OF_SCOPE
    ]
    return {
        "ledger_clear": not blocking,
        "blocking": [
            {
                "area": entry.area,
                "capability": entry.capability,
                "status": entry.status,
                "note": entry.note,
            }
            for entry in blocking
        ],
        "out_of_scope": list(OUT_OF_SCOPE),
        "criteria": list(GATE_CRITERIA),
        "ready": False,
        "reason": (
            "The ledger still has open entries, and the remaining gate "
            "criteria (a fixed comparison model set, a save_runtime_fem_state "
            "importer, and a performance comparison) have not been built."
        ),
    }


def parity_markdown() -> str:
    """The ledger as a report."""

    summary = parity_summary()
    gate = gate_status()
    counts = summary["counts"]

    lines = [
        "# ANYstructure parity ledger",
        "",
        f"Measured against {SOURCE}.",
        "",
        f"- capabilities tracked: {summary['total']}",
        f"- covered: {counts['covered']}",
        f"- partial: {counts['partial']}",
        f"- missing: {counts['missing']}",
        f"- coverage: {summary['coverage']:.0%}",
        "",
        "## Migration gate",
        "",
        f"**Ready: {'yes' if gate['ready'] else 'no'}.** {gate['reason']}",
        "",
        "Criteria:",
        "",
    ]
    lines += [f"{index}. {text}" for index, text in enumerate(GATE_CRITERIA, 1)]
    lines += [
        "",
        f"{len(gate['blocking'])} ledger entries are open (excluding "
        f"{len(OUT_OF_SCOPE)} that belong to ANYstructure rather than ANYfem).",
        "",
        "## Ledger",
        "",
    ]

    current_area = None
    for entry in LEDGER:
        if entry.area != current_area:
            current_area = entry.area
            lines += ["", f"### {current_area}", "", "| capability | status | note |", "| --- | --- | --- |"]
        mark = {"covered": "covered", "partial": "partial", "missing": "missing"}[
            entry.status
        ]
        lines.append(f"| {entry.capability} | {mark} | {entry.note} |")

    lines += [
        "",
        "---",
        "",
        "A capability is marked covered only when ANYfem can do the same job. "
        "Marking something covered because it is nearly covered would defeat "
        "the purpose of keeping this ledger.",
        "",
    ]
    return "\n".join(lines)


def write_parity_report(directory: str | Path = "reports/parity") -> Dict[str, Path]:
    """Write the ledger as JSON and Markdown."""

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "parity.json"
    markdown_path = target / "parity.md"
    json_path.write_text(
        json.dumps(
            {
                "summary": parity_summary(),
                "gate": gate_status(),
                "entries": [
                    {
                        "area": entry.area,
                        "capability": entry.capability,
                        "status": entry.status,
                        "note": entry.note,
                    }
                    for entry in LEDGER
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    markdown_path.write_text(parity_markdown(), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - CLI
    """Print the parity ledger and write its report."""

    import argparse

    parser = argparse.ArgumentParser(
        description="Report ANYstructure parity and the migration gate."
    )
    parser.add_argument("--out", default="reports/parity")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)

    print(parity_markdown())
    if not args.no_save:
        written = write_parity_report(args.out)
        print(f"written: {written['markdown']}")
    # The gate is information, not a failure: an open ledger is the expected
    # state until the migration is actually ready.
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
