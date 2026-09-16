"""Headless checks for the Solve-panel-to-public-solver option bridge."""

from types import SimpleNamespace

from anyfem.ui.panels import SolvePanel


class _Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def _panel(analysis: str):
    captured = {}

    def solve(name, **options):
        captured.update(name=name, options=options)

    panel = object.__new__(SolvePanel)
    panel.app = SimpleNamespace(
        project=SimpleNamespace(load_cases={"default": object()}),
        solve=solve,
    )
    panel._analysis = _Value(analysis)
    panel._target = _Value("case: default")
    panel._analysis_options = {
        name: _Value(value)
        for name, value in {
            "batch_cases": "*", "modes": "6", "buckling_mode": "1",
            "steps": "10", "factor": "1", "arc_steps": "60",
            "dt": "0.0002", "t_end": "0.02", "damping": "0",
            "mass": "500", "radius": "0.15", "speed": "4",
            "start": "0 0 1", "direction": "0 0 -1",
            "imperfection": "5",
        }.items()
    }
    panel._advanced_options = {
        name: _Value(value)
        for name, value in {
            "modal_shift": "0", "nonlinear_iterations": "25",
            "nonlinear_tolerance": "1e-6", "nonlinear_layers": "5",
            "nonlinear_min_step": "0.0009765625", "arc_tolerance": "1e-6",
            "arc_initial": "0.05", "arc_minimum": "0.0005",
            "arc_maximum": "0.2", "arc_load_scaling": "auto",
            "arc_rotation_scale": "auto", "arc_target_iterations": "5",
            "arc_growth": "1.25", "arc_cutback": "0.5",
            "arc_retries": "8", "arc_peak_steps": "4",
            "arc_peak_tolerance": "0.001", "arc_load_limit": "auto",
            "arc_post_peak": "auto", "arc_translation_limit": "auto",
            "arc_preload_steps": "10", "transient_beta": "0.25",
            "transient_gamma": "0.5", "transient_hht": "0",
            "rayleigh_beta": "0", "save_every": "1", "impact_dt": "auto",
            "impact_duration": "auto", "impact_rayleigh_alpha": "0",
            "steps_per_contact": "20", "steps_per_radius": "20",
            "post_contact_periods": "20", "capacity_half_wave": "4",
            "contact_surface": "midsurface",
        }.items()
    }
    panel._advanced_checks = {
        "stress_history": _Value(False), "skip_approach": _Value(True),
        "nonlinear_impact": _Value(False), "beam_contact": _Value(False),
    }
    panel._contact_surface = panel._advanced_options["contact_surface"]
    panel._kinematics = _Value("von_karman")
    panel._corotational_tangent = _Value("auto")
    panel._record_snapshots = _Value(True)
    panel._use_resources = _Value(False)
    panel._deterministic = _Value(True)
    panel._resource_options = {
        name: _Value("auto") for name in (
            "solver_threads", "assembly_threads", "recovery_threads",
            "process_workers", "memory_mib",
        )
    }
    return panel, captured


def test_arc_length_advanced_controls_reach_the_public_control_object():
    panel, captured = _panel("Arc length")
    panel._advanced_options["arc_post_peak"].value = "0.8"
    panel._advanced_options["arc_translation_limit"].value = "0.25"

    panel._solve()

    options = captured["options"]
    assert captured["name"] == "Arc length"
    assert options["control"].post_peak_load_fraction == 0.8
    assert options["control"].max_translation == 0.25
    assert options["max_iterations"] == 25
    assert options["arc_tolerance"] == 1.0e-6
    assert options["record_increment_snapshots"] is True


def test_transient_and_impact_options_are_not_silently_dropped():
    transient, captured = _panel("Transient")
    transient._advanced_options["transient_hht"].value = "-0.1"
    transient._advanced_checks["stress_history"].value = True
    transient._solve()
    assert captured["options"]["hht_alpha"] == -0.1
    assert captured["options"]["include_stress_history"] is True

    impact, captured = _panel("Impact")
    impact._advanced_options["impact_dt"].value = "0.00001"
    impact._advanced_options["contact_surface"].value = "top"
    impact._advanced_checks["beam_contact"].value = True
    impact._solve()
    assert captured["options"]["dt"] == 1.0e-5
    assert captured["options"]["contact"].contact_surface == "top"
    assert captured["options"]["contact"].beam_contact is True


def test_capacity_mode_and_resource_override_are_recorded():
    panel, captured = _panel("Capacity")
    panel._analysis_options["buckling_mode"].value = "2"
    panel._use_resources.value = True
    panel._resource_options["solver_threads"].value = "3"
    panel._resource_options["memory_mib"].value = "256"

    panel._solve()

    options = captured["options"]
    assert options["buckling_mode_number"] == 2
    assert options["mesh_min_elements_per_half_wave"] == 4
    assert options["resources"].solver_threads == 3
    assert options["resources"].memory_limit_bytes == 256 * 1024 * 1024
