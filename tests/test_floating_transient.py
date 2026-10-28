"""Connected floating transient: rigid transient -> physical nodal loads -> owner closure -> residual relief.

Manufactured but independently valid operators on the real assembled bar model of test_floating_residual_relief:
panel->nodal mapping T is arbitrary; the radiation / hydrostatic panel pressures are built so that the virtual-work
projection R^T T reproduces -A_inf and -K_hs exactly (pseudo-inverse), i.e. every generalised term has a matching
structural force distribution.  Analytic references: M_dry blocks against the owner mass properties, K_g = -m [g0]x,
attachment-load virtual work, and the zero generalised residual of the total frame load.
"""
import numpy as np
import pytest

from anysolver import BeamElement, FEModel
from anysolver.floating_transient import numeric_array_digest

from anyfem.application import floating_transient as ft
from anyfem.application.floating_loads import nodal_wrench
from anyfem.application.floating_mass import dry_mass_properties

DT = 0.01


def bar(n=4):
    model = FEModel("floating-transient-bar")
    model.add_material("steel", 1.0e7, 0.3, density=5.0)
    for i, x in enumerate(np.linspace(0.0, 2.0, n + 1), 1):
        model.add_node(i, x, 0.0, 0.0)
    for i in range(1, n + 1):
        model.add_element(i, BeamElement(i, [i, i + 1], "steel", {"area": 0.2, "Iy": 0.001, "Iz": 0.002, "J": 0.001,
                                                                    "consistent_mass": True}))
    return model


QPW = None        # world coordinates of the single guard query point (set by the world fixture)


def field_kernel(Pa, A_inf, n_p, identity=None):
    from anyhydro.radiation_field_kernel import RadiationFieldKernel
    kernel = np.zeros((2, 6, 6))
    if identity is None:
        identity = {} if QPW is None else {"query_points_world": QPW}
    return RadiationFieldKernel(
        kernel_hash="0" * 64, settings={}, dof_keys=tuple(f"d{i}" for i in range(6)), time=np.array([0.0, DT]),
        panel_ids=tuple(f"p{i}" for i in range(n_p)), query_point_ids=("w0",), pressure_a=Pa,
        pressure_chunks=(np.zeros((2, n_p, 6)),), elevation_a=np.zeros((1, 6)), elevation_chunks=(np.zeros((2, 1, 6)),),
        projection=np.zeros((6, n_p)), added_mass_infinite=A_inf, diagnostics={}, identity=identity, chunk_size=n_p,
        kernel_digest=numeric_array_digest(kernel), ainf_digest=numeric_array_digest(A_inf))


@pytest.fixture(scope="module")
def world():
    global QPW
    model = bar()
    dry = dry_mass_properties(model)
    QPW = [(np.array([1.0, 0.0, 0.0]) + np.asarray(dry.cg, float)).tolist()]   # Q0 = I, prepared pose t = 0
    n = len(dry.node_ids)
    R = ft.rigid_map(dry.points, dry.cg)
    n_p = 8
    rng = np.random.default_rng(0)
    T = rng.standard_normal((n, 6, n_p)) * 0.1
    G = np.einsum("nki,nkp->ip", R, T)
    Gp = np.linalg.pinv(G)
    A_inf = np.diag([1.0, 1.0, 2.0, 0.3, 0.4, 0.2])
    K_hs = np.diag([0.0, 0.0, 30.0, 5.0, 6.0, 0.0])
    Pa, Hs = -Gp @ A_inf, -Gp @ K_hs
    t = np.arange(101) * DT
    F_target = 0.5 * np.sin(2.0 * t)[:, None] * np.array([0.2, 0.0, 1.0, 0.0, 0.1, 0.0])[None, :]
    p_wave = F_target @ Gp.T
    fk = field_kernel(Pa, A_inf, n_p)
    W_g = np.r_[dry.mass * np.array([0.0, 0.0, -9.81]), 0.0, 0.0, 0.0]     # gravity wrench about the dry CG
    p0 = -Gp @ W_g                                                           # mean pressure balancing gravity
    return dict(model=model, dry=dry, R=R, T=T, G=G, Gp=Gp, A_inf=A_inf, K_hs=K_hs, Pa=Pa, Hs=Hs, t=t,
                F_target=F_target, p_wave=p_wave, fk=fk, n=n, W_g=W_g, p0=p0)


def make_input(w, **over):
    args = dict(times=w["t"], frame_indices=np.arange(0, w["t"].size, 10), Q0=np.eye(3), cg_body=w["dry"].cg,
                gravity=9.81, length_scale=2.0, node_ids=w["dry"].node_ids, mass_digest=ft.mass_identity(w["dry"]),
                added_mass_infinite=w["A_inf"], radiation_kernel=np.zeros((2, 6, 6)), kernel_horizon=DT,
                hydrodynamic_verification="accepted", hydro_admission="strict", panel_to_nodal=w["T"],
                hydrostatic_pressure=w["Hs"], field_kernel_hash=w["fk"].content_hash(), wave_pressure=w["p_wave"],
                waterline_points_body=np.array([[1.0, 0.0, 0.0]]) - 0.0, waterline_limit=np.array([10.0]),
                waterline_point_ids=("w0",), incident_elevation=np.zeros((w["t"].size, 1)),
                identities={"model": "bar", "coarse_prepare": {"pose_t": [0.0, 0.0, 0.0]}},
                mean_panel_pressure=w["p0"])
    args.update(over)
    # manufactured exact hydrostatics: the nodal fields follow the (overridable) manufactured panel fields
    if "mean_hydrostatic_nodal" not in over and args.get("mean_panel_pressure") is not None:
        args["mean_hydrostatic_nodal"] = np.einsum("nkp,p->nk", args["panel_to_nodal"], args["mean_panel_pressure"])
    if "hydrostatic_nodal" not in over and args.get("hydrostatic_pressure") is not None:
        args["hydrostatic_nodal"] = np.einsum("nkp,pj->jnk", args["panel_to_nodal"], args["hydrostatic_pressure"])
    if "wave_provenance" not in over and args.get("wave_pressure") is not None:
        # manufactured histories are labelled IMPORTED (never presented as native-bound)
        args["wave_provenance"] = ft.wave_history_provenance(
            kind="imported", times=args["times"], wave_pressure=args["wave_pressure"],
            incident_elevation=args.get("incident_elevation"), panel_ids=[f"p{i}" for i in range(8)],
            query_ids=list(args.get("waterline_point_ids") or ()), source="manufactured test fixture")
    return ft.FloatingTransientInput(**args)


def canonical_mean(w, *, mean_thrust=0.0, hub_body=None, rotor_axis=None, curve=None, wind_ref=None, fairleads=(),
                   forces=None, external_channels=None, Q=np.eye(3)):
    """Manufactured canonical operating-mean record with the full binding (as ANYstability writes it)."""
    return {"identity": "canonical-test-mean", "status": "converged_canonical_development",
            "kind": "canonical_full_mean_wrench", "mean_thrust": float(mean_thrust),
            "rotation_body_to_world": np.asarray(Q, float).tolist(), "translation_world": [0.0, 0.0, 0.0],
            "binding": {"cg_body": np.asarray(w["dry"].cg, float).tolist(),
                        "rotor_axis_body": None if rotor_axis is None else list(rotor_axis),
                        "hub_body": None if hub_body is None else np.asarray(hub_body, float).tolist(),
                        "thrust_curve": curve, "wind_reference_mean_world": wind_ref,
                        "fairleads": list(fairleads), "mooring_mean_forces_world": forces or {},
                        "external_channels": external_channels}}


def screening_record(w, frequencies=(1000.0,), **over):
    """MANUFACTURED screening fixture for code-path tests (not the bar's physical modes, not evidence)."""
    args = dict(structural_digest=ft.structural_identity(w["model"]), frequencies_hz=frequencies,
                n_rigid_modes_excluded=6, source="manufactured screening fixture", verified=True,
                scope="test fixture only")
    args.update(over)
    return ft.ElasticModalRecord(**args)


def run(w, inp, **kw):
    kw.setdefault("screening", screening_record(w))
    return ft.run_floating_transient(inp, fe_model=w["model"], dry_mass=w["dry"], field_kernel=kw.pop("fk", w["fk"]), **kw)


# ----------------------------------------------------------------------------- operators

def test_rigid_map_virtual_work_equals_owner_nodal_wrench(world):
    rng = np.random.default_rng(1)
    L = rng.standard_normal((world["n"], 6))
    assert np.allclose(ft.generalized(world["R"], L), nodal_wrench(world["dry"].points, L, world["dry"].cg), atol=1e-13)
    q = rng.standard_normal(6)
    u = np.einsum("nkj,j->nk", world["R"], q)
    r = world["dry"].points - world["dry"].cg
    assert np.allclose(u[:, :3], q[:3] + np.cross(q[3:], r)) and np.allclose(u[:, 3:], q[3:])


def test_dry_mass_and_weight_stiffness_match_owner_and_analytic(world):
    ops = ft._Operators(make_input(world), world["dry"])
    m = world["dry"].mass
    assert np.allclose(ops.M6[:3, :3], m * np.eye(3), rtol=1e-12)
    assert np.allclose(ops.M6[3:, 3:], world["dry"].inertia_cg, rtol=1e-10, atol=1e-12)
    assert np.allclose(ops.M6[:3, 3:], 0.0, atol=1e-12)                      # about the CG: no static moment
    g0 = np.array([0.0, 0.0, -9.81])
    Kg = np.zeros((6, 6)); Kg[:3, 3:] = -m * np.array([[0, -g0[2], g0[1]], [g0[2], 0, -g0[0]], [-g0[1], g0[0], 0]])
    assert np.allclose(ops.K_g, Kg, atol=1e-10)
    assert np.allclose(ops.K_hs, world["K_hs"], atol=1e-9)                    # mapped hydrostatic pressure, exact by design


# ----------------------------------------------------------------------------- connected run

def test_accepted_run_completes_with_closure_relief_and_full_history(world):
    res = run(world, make_input(world))
    assert res.status == "complete" and res.accepted_structural_output and res.hydrodynamic_verification == "accepted"
    assert res.times.size == world["t"].size and res.frame_indices.size == 11
    assert all(c["passed"] for c in res.diagnostics["closure"])
    assert res.frame_displacements.shape[0] == 11 and np.all(np.isfinite(res.frame_displacements))
    # the total frame load is self-balanced: zero generalised resultant (residual relief correction ~ 0)
    for f in range(11):
        tot = ft.generalized(world["R"], res.frame_loads["total"][f])
        scale = max(np.abs(ft.generalized(world["R"], res.frame_loads["inertia"][f])).max(), 1e-12)
        assert np.max(np.abs(tot)) <= 1e-8 * scale + 1e-12
    assert np.max(np.abs(res.frame_correction_acceleration)) < 1e-6
    assert res.diagnostics["waterline"]["samples"] == world["t"].size
    # the rigid EOM is the independent linear ODE with M_dry + A_inf and K_hs + K_g (zero memory kernel)
    ops = ft._Operators(make_input(world), world["dry"])
    q, a = res.displacements, res.accelerations
    lhs = q @ (ops.K_hs + ops.K_g).T + a @ (ops.M6 + world["A_inf"]).T
    assert np.allclose(lhs, world["F_target"], atol=1e-8)


def test_structural_window_never_restarts_the_physics(world):
    full = run(world, make_input(world))
    part = run(world, make_input(world, frame_indices=np.array([30, 70])))
    assert np.array_equal(part.displacements, full.displacements)
    i30, i70 = list(full.frame_indices).index(30), list(full.frame_indices).index(70)
    assert np.allclose(part.frame_loads["total"][0], full.frame_loads["total"][i30])
    assert np.allclose(part.frame_loads["total"][1], full.frame_loads["total"][i70])


def test_unverified_hydro_is_labelled_and_never_accepted(world):
    A = world["A_inf"]
    binding = dict(kernel_digest=numeric_array_digest(np.zeros((2, 6, 6))), ainf_digest=numeric_array_digest(A),
                   kernel_dt=DT)
    inp = make_input(world, hydrodynamic_verification="unverified", hydro_admission="development_unverified",
                     unpassed_hydro_gates=("source_passivity_omega_1",), admission_omegas=np.array([0.5, 1.0, 2.0]),
                     native_source_binding=binding)
    res = run(world, inp)
    assert res.status == "completed_unverified_hydrodynamics" and not res.accepted_structural_output
    assert "source_passivity_omega_1" in res.unpassed_gates
    with pytest.raises(ValueError):
        make_input(world, hydrodynamic_verification="unverified", hydro_admission="strict",
                   unpassed_hydro_gates=("x",))
    with pytest.raises(ValueError):
        make_input(world, hydrodynamic_verification="accepted", hydro_admission="development_unverified")
    with pytest.raises(ValueError):
        make_input(world, hydrodynamic_verification="unverified", hydro_admission="development_unverified")


def test_attachment_loads_conserve_the_generalised_forces(world):
    from anyloads.external_history import AffineFairleadMooring, FairleadSpec
    dry, R = world["dry"], world["R"]
    pts = dry.points
    fl = dict(identity="line-A", point_body=(pts[0] - dry.cg + np.array([0.0, 0.1, 0.0])).tolist(),
              pretension_world=[-0.5, 0.0, 0.2], stiffness=(np.diag([40.0, 40.0, 5.0])).tolist(),
              damping=(np.diag([1.0, 1.0, 0.5])).tolist(), node_id=int(dry.node_ids[0]))
    ext_t = np.array([0.0, 2.0])
    hub = pts[-1] - dry.cg + np.array([0.0, 0.0, 0.5])
    f0 = np.array(fl["pretension_world"])
    W_mean = world["W_g"] + np.r_[f0, np.cross(fl["point_body"], f0)] + 0.8 * np.r_[[1.0, 0, 0], np.cross(hub, [1.0, 0, 0])]
    law = dict(identity="line-A", point_body=(np.asarray(fl["point_body"]) + dry.cg).tolist(),   # absolute body point
               pretension_world=[-0.5, 0.0, 0.2], stiffness_world=fl["stiffness"], damping_world=fl["damping"])
    record = canonical_mean(world, mean_thrust=0.8, hub_body=hub + dry.cg, rotor_axis=[1.0, 0.0, 0.0],
                            curve={"speeds": [0.0, 20.0], "thrusts": [0.0, 2.0]}, wind_ref=[8.0, 0.0, 0.0],
                            fairleads=(law,), forces={"line-A": fl["pretension_world"]})
    inp = make_input(world, fairleads=(fl,), mooring_validity=np.full(6, 10.0),
                     mean_panel_pressure=-world["Gp"] @ W_mean, mean_equilibrium=record,
                     wind_reference_mean_world=np.array([8.0, 0.0, 0.0]),
                     wind_times=np.array([0.0, 2.0]), wind_velocity=np.array([[8.0, 0.0, 0.0], [8.0, 0.0, 0.0]]),
                     thrust_speeds=np.array([0.0, 20.0]), thrust_values=np.array([0.0, 2.0]),
                     hub_offset_body=hub, rotor_axis_body=np.array([1.0, 0, 0]),
                     hub_node_id=int(dry.node_ids[-1]), mean_thrust=0.8,
                     ext_times=ext_t, ext_values=np.array([[0.1, 0.05], [0.1, -0.05]]), ext_components=("FY", "MZ"),
                     ext_frames=("world", "body"), ext_points_body=np.array([pts[2] - dry.cg, [0, 0, 0]]),
                     ext_node_ids=(int(dry.node_ids[2]), int(dry.node_ids[1])))
    res = run(world, inp)
    assert res.status == "complete", res.message
    moor = AffineFairleadMooring((FairleadSpec(fl["identity"], fl["point_body"], fl["pretension_world"], fl["stiffness"],
                                               fl["damping"]),), np.eye(3), validity_half_range=np.full(6, 10.0))
    ext = ft._external_history(inp)
    for f, k in enumerate(res.frame_indices):
        q, v = res.displacements[k], res.velocities[k]
        assert np.allclose(ft.generalized(R, res.frame_loads["mooring"][f]),
                           -moor.stiffness_matrix() @ q - moor.damping_matrix() @ v, atol=1e-12)
        b = np.r_[[1.0, 0, 0], np.cross(inp.hub_offset_body, [1.0, 0, 0])]
        assert np.allclose(ft.generalized(R, res.frame_loads["turbine"][f]), b * (res.thrust[k] - 0.8), atol=1e-12)
        tk = res.times[k]
        expect = ext.generalized_force([tk], np.eye(3))[0] + ext.load_tangent([tk], np.eye(3))[0] @ q
        assert np.allclose(ft.generalized(R, res.frame_loads["external"][f]), expect, atol=1e-12)


# ----------------------------------------------------------------------------- refusals / non-success

def test_stale_inputs_are_refused_before_integration(world):
    with pytest.raises(ft.FloatingTransientWorkflowError, match="mass identity"):
        run(world, make_input(world, mass_digest="0" * 64))
    with pytest.raises(ft.FloatingTransientWorkflowError, match="CG"):
        run(world, make_input(world, cg_body=world["dry"].cg + 1e-3))
    with pytest.raises(ft.FloatingTransientWorkflowError, match="field_kernel_hash"):
        run(world, make_input(world, field_kernel_hash="1" * 64))
    other = field_kernel(world["Pa"], world["A_inf"] * 1.01, 8)
    with pytest.raises(Exception):          # digest-bound field kernel of another A_inf is refused
        run(world, make_input(world, field_kernel_hash=other.content_hash()), fk=other)


def test_inconsistent_pressure_distribution_fails_closure_before_relief(world):
    bad = field_kernel(world["Pa"] * 0.5, world["A_inf"], 8)            # mapped pressure no longer matches A_inf
    called = []

    def relief(*a, **k):
        called.append(1)
        return iter(())

    res = run(world, make_input(world, field_kernel_hash=bad.content_hash()), fk=bad, relief=relief)
    assert res.status == "closure_failed" and not called and not res.accepted_structural_output
    assert res.frame_loads["total"].shape[0] == 11                       # diagnostic loads retained


def test_trajectory_and_waterline_non_success_produce_no_structural_frames(world):
    called = []
    relief = lambda *a, **k: called.append(1) or iter(())
    tiny = make_input(world, waterline_limit=np.array([1e-9]))
    res = run(world, tiny, relief=relief)
    assert res.status == "waterline_guard_failed" and res.frame_indices.size == 0 and not called
    big = make_input(world, wave_pressure=world["p_wave"] * 1e4)             # drives the rotation past 5 degrees
    res = run(world, big, relief=relief)
    assert res.status != "complete" and res.frame_indices.size == 0 and not called


def test_hydro_input_fields_bind_actual_operators_and_select_the_admission(world):
    fields = ft.hydro_input_fields(radiation_kernel=np.zeros((2, 6, 6)), added_mass_infinite=world["A_inf"], dt=DT,
                                   field_kernel=world["fk"], verification="accepted", kernel_horizon=DT)
    assert fields["hydro_admission"] == "strict" and fields["field_kernel_hash"] == world["fk"].content_hash()
    dev = ft.hydro_input_fields(radiation_kernel=np.zeros((2, 6, 6)), added_mass_infinite=world["A_inf"], dt=DT,
                                field_kernel=world["fk"], verification="unverified", kernel_horizon=DT,
                                unpassed_gates=("source_passivity",), admission_omegas=[0.5, 1.0])
    assert dev["hydro_admission"] == "development_unverified" and dev["native_source_binding"]["kernel_dt"] == DT
    res = run(world, make_input(world, **dev))
    assert res.status == "completed_unverified_hydrodynamics" and "source_passivity" in res.unpassed_gates
    with pytest.raises(ft.FloatingTransientWorkflowError, match="digests"):
        ft.hydro_input_fields(radiation_kernel=np.zeros((2, 6, 6)), added_mass_infinite=world["A_inf"] * 2, dt=DT,
                              field_kernel=world["fk"], verification="accepted")
    with pytest.raises(ft.FloatingTransientWorkflowError, match="dt"):
        ft.hydro_input_fields(radiation_kernel=np.zeros((2, 6, 6)), added_mass_infinite=world["A_inf"], dt=2 * DT,
                              field_kernel=world["fk"], verification="accepted")
    with pytest.raises(ft.FloatingTransientWorkflowError, match="unpassed"):
        ft.hydro_input_fields(radiation_kernel=np.zeros((2, 6, 6)), added_mass_infinite=world["A_inf"], dt=DT,
                              field_kernel=world["fk"], verification="unverified", admission_omegas=[1.0])


def test_total_frames_are_mean_plus_perturbation_without_double_counting(world):
    res = run(world, make_input(world))
    R = world["R"]
    for f in range(res.frame_indices.size):
        L = res.frame_loads
        assert np.allclose(L["total"][f], L["mean_total"][f] + L["perturbation_total"][f], atol=1e-14)
        assert np.allclose(ft.generalized(R, L["mean_gravity"][f]), world["W_g"], atol=1e-10)   # gravity once, mean only
        assert np.allclose(ft.generalized(R, L["mean_total"][f]), 0.0, atol=1e-9)              # static balance
        assert np.allclose(L["perturbation_total"][f], L["pressure"][f] + L["weight"][f] + L["mooring"][f]
                           + L["turbine"][f] + L["external"][f] + L["inertia"][f], atol=1e-14)
    assert res.diagnostics["static_mean_balance"]["passed"]
    assert "quasistatic" in res.diagnostics["stress_label"] and res.frame_stresses.shape[0] == res.frame_indices.size
    assert np.allclose(res.metadata_arrays["frame_total_acceleration_cg"],
                       res.metadata_arrays["frame_primary_acceleration_cg"] + res.frame_correction_acceleration)


def test_static_mean_is_mandatory_and_must_balance(world):
    with pytest.raises(ft.FloatingTransientWorkflowError, match="mandatory"):
        make_input(world, mean_hydrostatic_nodal=None)
    with pytest.raises(ft.FloatingTransientWorkflowError, match="static mean"):
        run(world, make_input(world, mean_panel_pressure=np.zeros(8)))          # gravity unbalanced: refused
    wind = dict(wind_times=np.array([0.0, 2.0]), wind_velocity=np.array([[8.0, 0, 0], [8.0, 0, 0]]),
                thrust_speeds=np.array([0.0, 20.0]), thrust_values=np.array([0.0, 2.0]),
                hub_offset_body=np.zeros(3), rotor_axis_body=np.array([1.0, 0, 0]), hub_node_id=1, mean_thrust=0.8,
                wind_reference_mean_world=np.array([8.0, 0.0, 0.0]))
    with pytest.raises(ft.FloatingTransientWorkflowError, match="operating-mean"):
        make_input(world, **wind)
    with pytest.raises(ft.FloatingTransientWorkflowError, match="exactly"):
        make_input(world, **wind, mean_equilibrium=canonical_mean(world, mean_thrust=0.7))
    with pytest.raises(ft.FloatingTransientWorkflowError, match="reference mean wind"):
        make_input(world, **{k: v for k, v in wind.items() if k != "wind_reference_mean_world"})


# ----------------------------------------------------------------------------- adopted correctness regressions

def _turbine(world, **over):
    dry = world["dry"]
    hub = dry.points[-1] - dry.cg + np.array([0.0, 0.0, 0.5])
    args = dict(wind_times=np.array([0.0, 2.0]), wind_velocity=np.array([[8.0, 0, 0], [8.0, 0, 0]]),
                thrust_speeds=np.array([0.0, 20.0]), thrust_values=np.array([0.0, 2.0]), hub_offset_body=hub,
                rotor_axis_body=np.array([1.0, 0, 0]), hub_node_id=int(dry.node_ids[-1]), mean_thrust=0.8,
                wind_reference_mean_world=np.array([8.0, 0.0, 0.0]))
    args.update(over)
    return args, hub


def test_mean_binding_converts_the_absolute_hub_once_with_a_nonzero_cg(world):
    cg = np.asarray(world["dry"].cg, float)
    assert np.any(cg != 0.0)                                          # the bar CG is off the origin (x = 1)
    args, hub = _turbine(world)
    good = canonical_mean(world, mean_thrust=0.8, hub_body=hub + cg, rotor_axis=[1.0, 0, 0],
                          curve={"speeds": [0.0, 20.0], "thrusts": [0.0, 2.0]}, wind_ref=[8.0, 0.0, 0.0])
    make_input(world, **args, mean_equilibrium=good)
    stale_hub = dict(good, binding=dict(good["binding"], hub_body=hub.tolist()))   # CG-relative passed as absolute
    with pytest.raises(ft.FloatingTransientWorkflowError, match="hub"):
        make_input(world, **args, mean_equilibrium=stale_hub)
    other_wind = dict(good, binding=dict(good["binding"], wind_reference_mean_world=[9.0, 0.0, 0.0]))
    with pytest.raises(ft.FloatingTransientWorkflowError, match="reference mean wind"):
        make_input(world, **args, mean_equilibrium=other_wind)
    with pytest.raises(ft.FloatingTransientWorkflowError, match="canonical"):
        make_input(world, **args, mean_equilibrium=dict(good, kind="linear_offset"))


def test_mooring_pretension_must_be_the_force_evaluated_at_the_operating_pose(world):
    dry = world["dry"]
    fl = dict(identity="A", point_body=(dry.points[0] - dry.cg).tolist(), pretension_world=[-0.5, 0.0, 0.2],
              stiffness=np.diag([40.0, 40.0, 5.0]).tolist(), damping=np.zeros((3, 3)).tolist(),
              node_id=int(dry.node_ids[0]))
    law = dict(identity="A", point_body=dry.points[0].tolist(), pretension_world=[-0.6, 0.0, 0.2],
               stiffness_world=fl["stiffness"])
    rec = canonical_mean(world, fairleads=(law,), forces={"A": [-0.5, 0.0, 0.2]})   # evaluated (transported) force
    make_input(world, fairleads=(fl,), mooring_validity=np.full(6, 10.0), mean_equilibrium=rec)
    stale = canonical_mean(world, fairleads=(law,), forces={"A": [-0.6, 0.0, 0.2]})  # reference pretension reused
    with pytest.raises(ft.FloatingTransientWorkflowError, match="evaluated at the operating pose"):
        make_input(world, fairleads=(fl,), mooring_validity=np.full(6, 10.0), mean_equilibrium=stale)
    with pytest.raises(ft.FloatingTransientWorkflowError, match="validity"):
        make_input(world, fairleads=(fl,), mean_equilibrium=rec)                       # no late TypeError


def test_pitched_anisotropic_world_mooring_law_is_bound_in_mean_body_axes(world):
    from anyloads.external_history import AffineFairleadMooring, FairleadSpec
    dry = world["dry"]
    th = np.radians(4.0)
    Q = np.array([[np.cos(th), 0.0, np.sin(th)], [0.0, 1.0, 0.0], [-np.sin(th), 0.0, np.cos(th)]])
    Kw, Cw = np.diag([40.0, 40.0, 5.0]), np.diag([2.0, 2.0, 0.5])
    Kb, Cb = Q.T @ Kw @ Q, Q.T @ Cw @ Q
    assert abs(abs(Kb[0, 2]) - 35.0 * np.sin(th) * np.cos(th)) < 1e-12 and abs(Kb[0, 2]) > 2.4   # ~2.435, not 0
    law = dict(identity="A", point_body=dry.points[0].tolist(), pretension_world=[-0.5, 0.0, 0.2],
               stiffness_world=Kw.tolist(), damping_world=Cw.tolist())
    rec = canonical_mean(world, fairleads=(law,), forces={"A": [-0.5, 0.0, 0.2]}, Q=Q)
    fl = dict(identity="A", point_body=(dry.points[0] - dry.cg).tolist(), pretension_world=[-0.5, 0.0, 0.2],
              stiffness=Kb.tolist(), damping=Cb.tolist(), node_id=int(dry.node_ids[0]))
    make_input(world, Q0=Q, fairleads=(fl,), mooring_validity=np.full(6, 10.0), mean_equilibrium=rec)
    with pytest.raises(ft.FloatingTransientWorkflowError, match="body stiffness"):        # verbatim WORLD K refused
        make_input(world, Q0=Q, fairleads=(dict(fl, stiffness=Kw.tolist()),), mooring_validity=np.full(6, 10.0),
                   mean_equilibrium=rec)
    with pytest.raises(ft.FloatingTransientWorkflowError, match="body damping"):
        make_input(world, Q0=Q, fairleads=(dict(fl, damping=Cw.tolist()),), mooring_validity=np.full(6, 10.0),
                   mean_equilibrium=rec)
    # analytic force / tangent: a small mean-body translation dx_b moves the fairlead by Q dx_b in world, the world law
    # gives dF_w = -Kw Q dx_b, i.e. the body force -Q^T Kw Q dx_b = -Kb dx_b (and likewise for damping)
    moor = AffineFairleadMooring((FairleadSpec("A", fl["point_body"], fl["pretension_world"], fl["stiffness"],
                                               fl["damping"]),), Q, validity_half_range=np.full(6, 10.0))
    dx = np.array([1e-3, 0.0, 0.0])
    q = np.r_[dx, np.zeros(3)]
    analytic = -Q.T @ Kw @ Q @ dx
    assert np.allclose(-moor.stiffness_matrix()[:3, :3] @ dx, analytic, atol=1e-14)
    assert abs(analytic[2]) > 1e-3 * 2.4                                                    # anisotropic coupling
    vel = np.r_[np.array([0.0, 0.0, 1e-2]), np.zeros(3)]
    assert np.allclose(-moor.damping_matrix()[:3, :3] @ vel[:3], -Q.T @ Cw @ Q @ vel[:3], atol=1e-14)
    assert q.shape == (6,)


def test_wave_history_provenance_binds_arrays_ids_and_source(world):
    base = make_input(world)
    assert base.wave_provenance["kind"] == "imported"
    res = run(world, base)
    assert "IMPORTED" in res.diagnostics["wave_history"]["label"]
    with pytest.raises(ft.FloatingTransientWorkflowError, match="provenance"):           # same shape, other values
        make_input(world, wave_pressure=world["p_wave"] * 1.01, wave_provenance=base.wave_provenance)
    with pytest.raises(ft.FloatingTransientWorkflowError, match="explicit immutable wave_provenance"):
        make_input(world, wave_provenance=None)
    wrong_ids = dict(base.wave_provenance, panel_ids=[f"q{i}" for i in range(8)])       # same count, wrong source
    with pytest.raises(ft.FloatingTransientWorkflowError, match="wrong source"):
        run(world, make_input(world, wave_provenance=wrong_ids))
    native_elsewhere = ft.wave_history_provenance(
        kind="native_bound", times=world["t"], wave_pressure=world["p_wave"],
        incident_elevation=np.zeros((world["t"].size, 1)), panel_ids=[f"p{i}" for i in range(8)], query_ids=["w0"],
        source="other family", geometry_digest="0" * 64, pose_t=[0.0, 0.0, 0.0], Q0=np.eye(3),
        realization_digest="r" * 64)
    with pytest.raises(ft.FloatingTransientWorkflowError, match="another source geometry"):
        run(world, make_input(world, wave_provenance=native_elsewhere))


def test_recorded_pointwise_bound_must_be_the_actual_guard_envelope(world):
    from anyhydro.radiation_field_kernel import numeric_array_digest
    bound = np.full((1, 6), 1e-4)
    ident = {"query_points_world": QPW, "boundary_policy": "exact_dirichlet_z0", "policy_status": "admitted",
             "native_boundary_status": "failed", "pointwise_bound_digest": numeric_array_digest(bound)}
    fk = field_kernel(world["Pa"], world["A_inf"], 8, identity=ident)
    with pytest.raises(ft.FloatingTransientWorkflowError, match="not zeros"):            # None never becomes zeros
        run(world, make_input(world, field_kernel_hash=fk.content_hash()), fk=fk)
    with pytest.raises(ft.FloatingTransientWorkflowError, match="digest mismatch"):
        run(world, make_input(world, field_kernel_hash=fk.content_hash(), waterline_bound=bound * 2), fk=fk)
    res = run(world, make_input(world, field_kernel_hash=fk.content_hash(), waterline_bound=bound), fk=fk)
    assert res.status == "complete" and res.diagnostics["waterline"]["bound_envelope_included"]
    assert ft.field_kernel_failures(fk) == []                     # raw failed + admitted policy: not a failure


def test_rigid_and_field_limits_are_forwarded_and_tiny_max_steps_refuses_before_integration(world, monkeypatch):
    import anysolver.floating_transient as sft
    import anyhydro.radiation_field_kernel as rfk
    import anyhydro.floating_transient_fields as ftf
    seen = {}
    real_solve, real_field, real_guard = sft.solve_floating_transient, rfk.field_history_from_transient, \
        ftf.guarded_clearance

    def solve(spec, **kw):
        seen["rigid"] = kw.get("resource_config")
        return real_solve(spec, **kw)

    def field(fk, res, kind, **kw):
        seen.setdefault("field", []).append(kw.get("limits"))
        return real_field(fk, res, kind, **kw)

    def guard(*a, **kw):
        seen["guard"] = kw.get("max_work_bytes")
        return real_guard(*a, **kw)
    monkeypatch.setattr(sft, "solve_floating_transient", solve)
    monkeypatch.setattr(rfk, "field_history_from_transient", field)
    monkeypatch.setattr(ftf, "guarded_clearance", guard)
    limit = 64 * 1024 * 1024
    run(world, make_input(world), max_work_bytes=limit, rigid_limits={"max_operations": 1.0e10})
    assert seen["rigid"].max_working_bytes == limit and seen["rigid"].max_operations == 1.0e10
    assert all(lim.max_work_bytes == limit for lim in seen["field"]) and seen["guard"] == limit
    seen.clear()
    with pytest.raises(ft.FloatingTransientWorkflowError, match="max_steps"):
        run(world, make_input(world), rigid_limits={"max_steps": 10})
    assert "rigid" not in seen and "field" not in seen
    with pytest.raises(ft.FloatingTransientWorkflowError, match="unknown rigid limits"):
        run(world, make_input(world), rigid_limits={"max_whatever": 1})


def test_q8_shell_stress_layout_is_counted_from_the_owner_contract():
    from anysolver import FEModel, ShellElement
    m = FEModel("q8-layout")
    m.add_material("steel", 1e7, .3, density=5.)
    for i, (x, y) in enumerate([(-1, -1), (1, -1), (1, 1), (-1, 1), (0, -1), (1, 0), (0, 1), (-1, 0)], 1):
        m.add_node(i, x, y, 0.)
    el = ShellElement(1, list(range(1, 9)), "steel", thickness=.1)
    m.add_element(1, el)
    count = ft.element_stress_value_count(m, el)
    assert count >= 99                                   # 9 Gauss points x 11 arrays (more with return_global)
    assert ft.stress_values_per_frame(m) == count and ft.STRESS_VALUES_PER_ELEMENT_BOUND >= 99


def test_static_closure_is_strict_and_separate_from_the_dynamic_criterion(world):
    # 0.5% hydrostatic mismatch would pass the 1% dynamic closure; the static 1e-8 closure refuses it before integration
    called = []
    with pytest.raises(ft.FloatingTransientWorkflowError, match="static mean"):
        run(world, make_input(world, mean_panel_pressure=world["p0"] * 1.005), relief=lambda *a, **k: called.append(1))
    assert not called
    rep = ft.static_mean_closure(world["R"], {"a": np.ones((world["n"], 6)), "b": -np.ones((world["n"], 6))})
    assert rep["passed"] and rep["tolerance"] == 1e-8


def test_waterline_guard_uses_the_world_vertical_of_a_heeled_body_with_sway(world):
    heel = np.radians(4.0)
    Q = np.array([[1, 0, 0], [0, np.cos(heel), -np.sin(heel)], [0, np.sin(heel), np.cos(heel)]])
    p = np.array([[1.0, 2.0, 0.3]])
    q = np.array([[0.0, 0.7, 0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.2, 0.01, 0.0, 0.0]])
    ez_b = Q[2]
    expect = q[:, :3] @ ez_b + (np.cross(q[:, 3:], p[0]) @ Q.T)[:, 2]
    got = q[:, :3] @ ez_b[:, None] + q[:, 3:] @ np.cross(p, ez_b[None, :]).T
    np.testing.assert_allclose(got[:, 0], expect, atol=1e-15)
    assert abs(got[0, 0]) > 0.04                           # pure body sway moves a heeled waterline point vertically


def test_physical_runs_require_the_waterline_guard_and_wave_elevation(world):
    with pytest.raises(ft.FloatingTransientWorkflowError, match="waterline guard is mandatory"):
        make_input(world, waterline_points_body=None, waterline_limit=None, waterline_point_ids=())
    with pytest.raises(ft.FloatingTransientWorkflowError, match="incident elevation"):
        make_input(world, incident_elevation=None)
    with pytest.raises(ft.FloatingTransientWorkflowError, match="query points"):
        run(world, make_input(world, waterline_point_ids=("other",)))
    synthetic = make_input(world, waterline_points_body=None, waterline_limit=None, waterline_point_ids=(),
                           incident_elevation=None, waterline_guard_waiver="manufactured bar fixture")
    res = run(world, synthetic)
    assert res.status == ft.STATUS_SYNTHETIC_UNGUARDED and not res.accepted_structural_output


def test_world_fixed_mean_force_rotates_with_pitch_in_ode_and_structure(world):
    dry = world["dry"]
    F0 = 3.0
    pt = dry.points[2] - dry.cg
    ext = dict(ext_times=np.array([0.0, 2.0]), ext_values=np.zeros((2, 1)), ext_components=("FX",),
               ext_frames=("world",), ext_points_body=np.array([pt]), ext_node_ids=(int(dry.node_ids[2]),),
               ext_mean_values=np.array([F0]))
    rec = canonical_mean(world, external_channels=ft.external_channels_record(("FX",), ("world",), [pt],
                                                                              (int(dry.node_ids[2]),), [F0]))
    W_mean = world["W_g"] + np.r_[[F0, 0.0, 0.0], np.cross(pt, [F0, 0.0, 0.0])]
    inp = make_input(world, mean_panel_pressure=-world["Gp"] @ W_mean, mean_equilibrium=rec, **ext)
    ops = ft._Operators(inp, dry)
    # analytic: a world +X force seen in body axes after pitch theta_y is F0 (e_x - theta x e_x) = F0 (e_x + theta_y e_z)
    th = np.array([0.0, 1e-3, 0.0])
    nodal = np.einsum("j,jnk->nk", np.r_[np.zeros(3), th], ops.ext_mean_unit)
    gen = ft.generalized(world["R"], nodal)
    np.testing.assert_allclose(gen[:3], [0.0, 0.0, F0 * th[1]], atol=1e-15)
    np.testing.assert_allclose(gen[3:], np.cross(pt, [0.0, 0.0, F0 * th[1]]), atol=1e-14)
    np.testing.assert_allclose(ops.K_ext_mean @ np.r_[np.zeros(3), th], -gen, atol=1e-15)   # same term in the ODE
    res = run(world, inp)
    assert res.status == "complete", res.message
    for f, k in enumerate(res.frame_indices):
        q = res.displacements[k]
        exp = ft.generalized(world["R"], np.einsum("j,jnk->nk", q, ops.ext_mean_unit))
        assert np.allclose(ft.generalized(world["R"], res.frame_loads["external"][f]), exp, atol=1e-12)


def test_memory_preflight_counts_fields_and_every_frame_component(world):
    inp = make_input(world)
    n_el = len(world["model"].mesh.elements)
    rep = ft.transient_memory_preflight(inp, n_nodes=world["n"], n_elements=n_el, field_kernel=world["fk"])
    assert rep["frame_bytes"] == inp.frame_indices.size * world["n"] * 6 * 8 * ft.N_FRAME_COMPONENTS
    assert rep["field_bytes"] >= inp.times.size * 8 * 8
    assert rep["stored_input_bytes"] >= inp.panel_to_nodal.nbytes + inp.hydrostatic_nodal.nbytes
    assert rep["field_fft_bytes"] > 0
    assert rep["stress_bound_bytes"] == inp.frame_indices.size * n_el * ft.STRESS_VALUES_PER_ELEMENT_BOUND * 8
    with pytest.raises(ft.FloatingTransientWorkflowError, match="cannot be bounded"):
        ft.transient_memory_preflight(inp, n_nodes=world["n"])                 # stresses without an element count
    with pytest.raises(ft.FloatingTransientWorkflowError, match="exceeds"):
        ft.transient_memory_preflight(inp, n_nodes=world["n"], n_elements=n_el, max_work_bytes=1024)
    with pytest.raises(ft.FloatingTransientWorkflowError, match="only tighten"):
        ft.transient_memory_preflight(inp, n_nodes=world["n"], n_elements=n_el,
                                      max_work_bytes=ft.MAX_WORK_BYTES + 1)
    called = []
    import anysolver.floating_transient as sft
    import anyhydro.radiation_field_kernel as rfk
    orig_solve, orig_field = sft.solve_floating_transient, rfk.field_history_from_transient
    try:                                             # a tighter limit refuses BEFORE trajectory / field allocation
        sft.solve_floating_transient = lambda *a, **k: called.append("trajectory")
        rfk.field_history_from_transient = lambda *a, **k: called.append("field")
        with pytest.raises(ft.FloatingTransientWorkflowError, match="exceeds"):
            run(world, inp, max_work_bytes=1024, relief=lambda *a, **k: called.append(1))
    finally:
        sft.solve_floating_transient, rfk.field_history_from_transient = orig_solve, orig_field
    assert not called


def test_missing_or_failed_elastic_screening_is_never_accepted(world):
    res = run(world, make_input(world), screening=None)
    assert res.status == ft.STATUS_NOT_SCREENED and not res.accepted_structural_output
    assert res.diagnostics["elastic_screening"]["status"] == "not_assessed"
    assert "elastic_screening_not_assessed" in res.unpassed_gates
    # a bound low elastic frequency is REPORTED with an attention indicator (heuristic, not an accuracy gate)
    low = run(world, make_input(world), screening=screening_record(world, frequencies=(1e-3,)))
    scr_low = low.diagnostics["elastic_screening"]
    assert scr_low["status"] == "reported" and scr_low["indicator"] == "attention" and "heuristic" in scr_low["label"]
    # parent adoption: attention is never accepted output / 'all gates passed'; data stay inspectable
    assert low.status == ft.STATUS_SCREENING_ATTENTION and not low.accepted_structural_output
    assert "elastic_screening_attention" in low.unpassed_gates and low.frame_indices.size > 0
    assert "all gates passed" not in low.message
    foreign = run(world, make_input(world), screening=screening_record(world, structural_digest="0" * 64))
    assert foreign.diagnostics["elastic_screening"]["reason"] == "modal record belongs to another structural model"
    assert foreign.status == ft.STATUS_NOT_SCREENED
    unverified = run(world, make_input(world), screening=screening_record(world, verified=False))
    assert unverified.status == ft.STATUS_NOT_SCREENED
    ok = run(world, make_input(world))
    scr = ok.diagnostics["elastic_screening"]
    assert ok.status == "complete" and scr["status"] == "reported" and scr["loading_band_hz"] is not None
    spec = scr["loading_spectrum"]
    assert "radiation_panel_pressure" in spec["channels"] and "wave_panel_pressure" in spec["channels"]
    assert spec["omitted_energy_fraction"] is not None and spec["omitted_energy_fraction"] <= 0.01 + 1e-12


def test_self_equilibrated_high_frequency_panel_pressure_sets_the_loading_band():
    # zero rigid response, but a 30 Hz self-equilibrated (+p / -p) panel field: the band must see it
    t = np.arange(1001) * 1e-3
    zero = np.zeros((t.size, 6))
    p = np.sin(2 * np.pi * 30.0 * t)
    panels = np.column_stack([p, -p])
    rigid_only = ft.loading_spectrum(t, [("rigid_acceleration", zero)])
    assert rigid_only["n_channels"] == 0 and rigid_only["cutoff_hz"] == 0.0
    full = ft.loading_spectrum(t, [("rigid_acceleration", zero), ("panel_pressure", panels)])
    assert full["n_channels"] == 2 and full["cutoff_hz"] >= 29.0


def test_changed_modulus_with_the_same_mass_changes_the_structural_identity(world):
    from anysolver import BeamElement, FEModel
    model = FEModel("floating-transient-bar")
    model.add_material("steel", 2.0e7, 0.3, density=5.0)
    for i, x in enumerate(np.linspace(0.0, 2.0, 5), 1):
        model.add_node(i, x, 0.0, 0.0)
    for i in range(1, 5):
        model.add_element(i, BeamElement(i, [i, i + 1], "steel", {"area": 0.2, "Iy": 0.001, "Iz": 0.002, "J": 0.001,
                                                                    "consistent_mass": True}))
    assert ft.mass_identity(dry_mass_properties(model)) == ft.mass_identity(world["dry"])
    assert ft.structural_identity(model) != ft.structural_identity(world["model"])


def test_an_added_constraint_equation_with_the_same_k_and_m_is_a_different_structural_model(world):
    a, b = bar(), bar()
    assert ft.structural_identity(a) == ft.structural_identity(b)              # same content -> same identity
    d1, d2 = list(b.mesh.nodes[1].dofs), list(b.mesh.nodes[2].dofs)
    b.add_constraint_equation(terms=((int(d1[2]), 1.0), (int(d2[2]), -1.0)), source_id="test-mpc")
    from scipy import sparse
    from anysolver.matrix_assembly import assemble_stiffness_matrix
    Ka, Kb = assemble_stiffness_matrix(a)[0], assemble_stiffness_matrix(b)[0]
    assert abs(sparse.csr_matrix(Ka) - sparse.csr_matrix(Kb)).max() == 0.0      # unreduced K unchanged by the MPC
    assert ft.structural_identity(a) != ft.structural_identity(b)              # ... but the structural model differs


@pytest.mark.parametrize("identity,diagnostics", [
    ({"development": "unverified_hydrodynamics"}, {}),
    ({}, {"unpassed_gates": ["elevation_transfer_reconstruction"], "reconstruction": {"elevation": {"passed": False}}}),
])
def test_bound_field_failures_cannot_be_run_as_accepted_even_bypassing_the_helper(world, identity, diagnostics):
    from anyhydro.radiation_field_kernel import RadiationFieldKernel
    good = world["fk"]
    bad = RadiationFieldKernel(
        kernel_hash=good.kernel_hash, settings={}, dof_keys=good.dof_keys, time=good.time, panel_ids=good.panel_ids,
        query_point_ids=good.query_point_ids, pressure_a=good.pressure_a, pressure_chunks=good.pressure_chunks,
        elevation_a=good.elevation_a, elevation_chunks=good.elevation_chunks, projection=good.projection,
        added_mass_infinite=good.added_mass_infinite, diagnostics=diagnostics,
        identity=dict(identity, query_points_world=QPW), chunk_size=good.chunk_size, kernel_digest=good.kernel_digest,
        ainf_digest=good.ainf_digest)
    assert ft.field_kernel_failures(bad)
    with pytest.raises(ft.FloatingTransientWorkflowError, match="accepted"):
        run(world, make_input(world, field_kernel_hash=bad.content_hash()), fk=bad)
    with pytest.raises(ft.FloatingTransientWorkflowError, match="unverified|failed|development"):
        ft.hydro_input_fields(radiation_kernel=np.zeros((2, 6, 6)), added_mass_infinite=world["A_inf"], dt=DT,
                              field_kernel=bad, verification="accepted")
    dev = ft.hydro_input_fields(radiation_kernel=np.zeros((2, 6, 6)), added_mass_infinite=world["A_inf"], dt=DT,
                                field_kernel=bad, verification="unverified", unpassed_gates=("caller_gate",),
                                admission_omegas=[0.5, 1.0])
    assert set(ft.field_kernel_failures(bad)) <= set(dev["unpassed_hydro_gates"])     # actual bound failures propagate


def test_exact_zero_policy_status_is_judged_by_its_effective_policy():
    from types import SimpleNamespace
    raw_failed_policy_ok = SimpleNamespace(identity={"native_boundary_status": "failed",
                                                     "boundary_policy": "exact_dirichlet_z0", "policy_status": "admitted"},
                                           diagnostics={})
    assert ft.field_kernel_failures(raw_failed_policy_ok) == []            # not falsely promoted NOR always refused
    policy_failed = SimpleNamespace(identity={"native_boundary_status": "failed",
                                              "boundary_policy": "exact_dirichlet_z0", "policy_status": "failed"},
                                    diagnostics={})
    assert ft.field_kernel_failures(policy_failed) == ["policy_status:failed"]
    raw_only = SimpleNamespace(identity={"native_boundary_status": "unchecked"}, diagnostics={})
    assert ft.field_kernel_failures(raw_only) == ["native_boundary_status:unchecked"]


def test_guard_coordinates_must_be_the_kernels_bound_query_points(world):
    shifted = field_kernel(world["Pa"], world["A_inf"], 8,
                           identity={"query_points_world": [[QPW[0][0] + 0.5, QPW[0][1], QPW[0][2]]]})
    with pytest.raises(ft.FloatingTransientWorkflowError, match="bound query"):
        run(world, make_input(world, field_kernel_hash=shifted.content_hash()), fk=shifted)
    unbound = field_kernel(world["Pa"], world["A_inf"], 8, identity={})
    with pytest.raises(ft.FloatingTransientWorkflowError, match="does not bind"):
        run(world, make_input(world, field_kernel_hash=unbound.content_hash()), fk=unbound)


def test_batch_effective_load_stresses_match_the_scalar_reference(world):
    from anysolver import solve_inertia_relief
    from anysolver.recovery import RecoveryConfig, recover_stress_result
    from anyfem.solve import floating_batch as fb
    model, dry = world["model"], world["dry"]
    rng = np.random.default_rng(9)
    F = rng.normal(size=(world["n"], 6))
    cfg = RecoveryConfig(include_reactions=False, include_displacements=True, include_stresses=True)
    out = list(fb.iter_residual_relief(model, dry.node_ids, [fb.FloatingFrameInput("s0", 0.0, F)], recovery_config=cfg))[0]
    pure = solve_inertia_relief(model, fb.build_residual_load_case(model, dry.node_ids, F, "ref"))
    scalar = recover_stress_result(model, np.asarray(pure.displacements), cfg).element_stresses
    a, la = ft.flatten_stresses([out.element_stresses])
    b, lb = ft.flatten_stresses([scalar])
    assert la == lb and a.shape[1] > 0
    assert np.allclose(a, b, rtol=1e-7, atol=1e-9 * max(1.0, np.abs(b).max()))
    plain = list(fb.iter_residual_relief(model, dry.node_ids, [fb.FloatingFrameInput("s1", 0.0, F)]))[0]
    assert plain.element_stresses is None                                        # default behaviour unchanged


def test_relief_cancellation_is_an_explicit_non_success(world):
    class SolveCancelled(Exception):
        pass

    def relief(*a, **k):
        raise SolveCancelled("cancelled by user")
        yield  # pragma: no cover

    res = run(world, make_input(world), relief=relief)
    assert res.status == "cancelled" and not res.accepted_structural_output
