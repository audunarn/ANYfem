"""The experimental input format bounds bytes, not the number of B3 members."""

from dataclasses import replace

import numpy as np
import pytest

stiffened_shell = pytest.importorskip("anysolver.stiffened_shell")
from anysolver import b3_ge
from anyfem.solve import stiffened_shell as consumer


def _beams():
    e, nu = 210e9, .3
    g = e / (2 * (1 + nu))
    section = b3_ge.EllipsoidalGeneralizedSection(
        np.diag((e*.01, g*.008, g*.008, g*1.6666666666666667e-5,
                 e*8.333333333333333e-6, e*8.333333333333333e-6)),
        np.eye(6), 1e20, 1e9)
    return tuple(b3_ge.define_beam(
        b3_ge.SELECTOR, index + 1,
        (101 + 2*index, 102 + 2*index, 103 + 2*index),
        np.array(((index, 0., .0675), (index + .5, 0., .0675),
                  (index + 1., 0., .0675))),
        np.tile(np.eye(3), (3, 1, 1)), section, np.eye(6))
        for index in range(17))


def _panel(kind):
    beams = _beams()
    e, nu = 210e9, .3
    if kind == "point":
        shell = stiffened_shell.ShellPanel(
            (1, 2, 3, 4, 5, 6),
            ((0., -.5, 0.), (1., -.5, 0.), (2., -.5, 0.),
             (0., .5, 0.), (1., .5, 0.), (2., .5, 0.)),
            ((1, 2, 5, 4), (2, 3, 6, 5)), .07, e, nu, 7850.)
        return consumer.ExperimentalGEPointPanel(
            shell, beams, (stiffened_shell.PointConnection(1, 101),),
            tuple(stiffened_shell.FixedCoordinate("shell", node, axis)
                  for node in (1, 4) for axis in range(6)))
    shell = stiffened_shell.ShellPanel(
        tuple(range(1, 9)),
        tuple((x, y, 0.) for y in (0., .2) for x in (0., 1/3, 2/3, 1.)),
        ((1, 2, 6, 5), (2, 3, 7, 6), (3, 4, 8, 7)), .035, e, nu, 7850.)
    line = stiffened_shell.ExperimentalBL5Line(
        (1, 2, 3, 4), ((0, 0), (0, 1), (1, 0), (1, 1)))
    fixed = tuple(stiffened_shell.FixedCoordinate("shell", node, axis)
                  for node in (1, 5) for axis in range(6))
    return consumer.ExperimentalGEBL5Panel(shell, beams, (line,), fixed)


@pytest.mark.parametrize("kind", ("point", "bl5"))
def test_seventeen_native_members_construct_parse_and_reopen(kind, tmp_path):
    panel = _panel(kind)
    assert len(panel.beams) == 17
    record = panel.to_dict()
    assert len(record["beams"]) == 17
    assert type(panel).from_dict(record).to_dict() == record
    reopened = consumer.load_case(consumer.save_case(panel, tmp_path / "many.anyfem-ge.json"))
    assert reopened.to_dict() == record
    assert tuple(beam.sha256 for beam in reopened.beams) == tuple(
        beam.sha256 for beam in panel.beams)


def test_seventeen_members_reopen_through_shared_modal_binding_parser(tmp_path):
    point = _panel("point")
    modal = consumer.ExperimentalGEReferenceModalPanel(
        point.shell, point.beams, point.points, point.fixed,
        stiffened_shell.GEReferenceModesRequest(2, 1.))
    record = modal.to_dict()
    assert record["schema"] == consumer.SCHEMA_REFERENCE_MODAL
    assert consumer.ExperimentalGEReferenceModalPanel.from_dict(record).to_dict() == record
    reopened = consumer.load_case(consumer.save_case(
        modal, tmp_path / "modal-many.anyfem-ge.json"))
    assert reopened.to_dict() == record
    assert len(reopened.beams) == 17


@pytest.mark.parametrize("kind", ("point", "bl5"))
def test_empty_or_invalid_member_inventory_still_rejected(kind):
    panel = _panel(kind)
    with pytest.raises(ValueError, match="beams"):
        replace(panel, beams=())
    record = panel.to_dict()
    record["beams"] = []
    with pytest.raises(ValueError, match="beam list"):
        type(panel).from_dict(record)
    record = panel.to_dict()
    record["beams"][16] = {"raw_base64": "bad", "sha256": "0" * 64}
    with pytest.raises(ValueError):
        type(panel).from_dict(record)


@pytest.mark.parametrize("kind", ("point", "bl5"))
def test_byte_bound_applies_with_seventeen_members(kind, tmp_path, monkeypatch):
    panel = _panel(kind)
    path = tmp_path / "many.anyfem-ge.json"
    consumer.save_case(panel, path)
    assert path.stat().st_size < consumer.MAX_FILE_BYTES
    monkeypatch.setattr(consumer, "MAX_FILE_BYTES", path.stat().st_size - 1)
    with pytest.raises(ValueError, match="size limit"):
        consumer.load_case(path)
    with pytest.raises(ValueError, match="size limit"):
        consumer.save_case(panel, tmp_path / "too-large.anyfem-ge.json")
