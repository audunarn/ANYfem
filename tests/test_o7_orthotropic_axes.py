"""O7: orthotropic shell material axes must not depend on element node order.

docs/direction-programme/O7-orthotropic-axes.md.  The same flat plate meshed
twice, once with half the quads' node order cyclically rotated (same geometry
and normal, element-local x turned 90 deg), must give the same physical
answer.  Isotropic is a control and passes today.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docs" / "direction-programme" / "scripts"))
import o7_ortho_axes_probe as probe  # noqa: E402


def _max_translation(symmetry: str, rotate: bool) -> float:
    return probe.run(symmetry, rotate, 0.25)[2].max_translation()[1]


def test_isotropic_plate_is_independent_of_node_order_control():
    assert _max_translation("iso", True) == pytest.approx(_max_translation("iso", False), rel=1e-12)


@pytest.mark.xfail(strict=True, reason="O7: ANYfem passes no material_direction, so the orthotropic "
                   "1-axis follows element node order (measured +10 % deflection at 0.125 m)")
def test_orthotropic_plate_is_independent_of_node_order():
    assert _max_translation("ortho", True) == pytest.approx(_max_translation("ortho", False), rel=1e-6)
