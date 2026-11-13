"""Elevation origins used by drawing coordinates and plate schedules."""


def elevation_origin(profiles, datum):
    if datum not in {"support_base", "global"}:
        raise ValueError("Elevation datum must be support_base or global")
    return min(profile.z_min_m for profile in profiles) if datum == "support_base" else 0.0
