from __future__ import annotations

import numpy as np
from types import SimpleNamespace

from anyfem.post.history import Series
from anyfem.ui.result_display import (
    ENGINEERING_DISPLAY,
    converted_series,
    unit_transform,
)


def test_engineering_result_units_are_display_only():
    assert unit_transform("m", ENGINEERING_DISPLAY) == (1000.0, "mm")
    assert unit_transform("Pa", ENGINEERING_DISPLAY) == (1.0e-6, "MPa")
    assert unit_transform("rad", ENGINEERING_DISPLAY) == (1.0, "rad")


def test_history_conversion_does_not_mutate_stored_si_series():
    source = Series(
        "path",
        x=np.array([0.0, 0.012]),
        y=np.array([0.0, 400.0e6]),
        x_unit="m",
        y_unit="Pa",
    )

    converted = converted_series([source], ENGINEERING_DISPLAY)[0]

    assert converted.x.tolist() == [0.0, 12.0]
    assert converted.y.tolist() == [0.0, 400.0]
    assert converted.x_unit == "mm"
    assert converted.y_unit == "MPa"
    assert source.x.tolist() == [0.0, 0.012]
    assert source.y.tolist() == [0.0, 400.0e6]


def test_auto_deformation_policy_handles_missing_zero_and_retained_displacement():
    from anyfem.presentation.result_display import deformation_scale
    def missing_translation():raise KeyError("translations unavailable")
    missing=SimpleNamespace(max_translation=missing_translation)
    zero=SimpleNamespace(max_translation=lambda:(1,0.0))
    for text in ("auto","", " AUTO "):
        assert deformation_scale(text,None,missing)==0.0
        assert deformation_scale(text,None,zero)==1.0
        assert deformation_scale(text,None,None)==1.0
