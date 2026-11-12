"""View geometry shared by the workbench preview, print sheets and model-space CAD."""
from dataclasses import dataclass
from importlib import resources
import json
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class DrawingPath:
    layer: str
    points: tuple
    closed: bool = False
    label: str = ""


@dataclass(frozen=True)
class DrawingView:
    title: str
    paths: tuple[DrawingPath, ...]
    notes: tuple[str, ...] = ()
    exact_loops: tuple = ()
    projection: str = ""

    def extent(self):
        points = [np.asarray(path.points) for path in self.paths if len(path.points)]
        if not points:
            omitted = [note for note in self.notes if "omitted" in note.lower()]
            reason = " " + " ".join(omitted[:3]) if omitted else ""
            raise ValueError("There is no geometry in this drawing. Change the selection or plane limits." + reason)
        coordinates = np.concatenate(points)
        return coordinates.min(axis=0), coordinates.max(axis=0)


def load_support_b_example():
    from ..io import project_from_dict
    source = resources.files("anyfem").joinpath("example_data", "Support-B.anyfem")
    return project_from_dict(json.loads(source.read_text(encoding="utf-8")))


def write_view_dxf(view: DrawingView, path: Path):
    try:
        import ezdxf
    except ImportError as exc:
        raise ImportError("Install ANYfem[drawings] to export CAD drawing files.") from exc
    from .plate_curves import write_profiles_dxf

    low, high = view.extent()
    if view.exact_loops:
        write_profiles_dxf(path, view.exact_loops)
        doc = ezdxf.readfile(path)
    else:
        doc = ezdxf.new("R2010")
        doc.units = ezdxf.units.MM
        doc.header["$MEASUREMENT"] = 1
    msp = doc.modelspace()
    height = max(1., float(np.max(high-low))*1000/85)
    if "ANNOTATION" not in doc.layers:
        doc.layers.add("ANNOTATION", color=7)
    legend = []
    labels = {}
    plate = view.projection=="plate" or (not view.projection and any(p.layer=="OUTLINE" for p in view.paths))
    for curve in view.paths:
        pts = np.asarray(curve.points)*1000
        if curve.layer=="HOLES" and plate:
            left, right = pts.min(axis=0),pts.max(axis=0)
            centre = (left+right)/2 - low*1000
            size = right-left
            label = f"H{1+len(legend)}"
            legend.append(f"{label}    {size[0]:.2f} x {size[1]:.2f} mm    centre X {centre[0]:.2f}, Y {centre[1]:.2f} mm from lower-left extents")
            msp.add_text(label,height=height,dxfattribs={"layer":"ANNOTATION"}).set_placement((left+right)/2)
        if view.exact_loops and curve.layer in {"OUTLINE", "HOLES"}:
            continue
        if curve.layer not in doc.layers:
            doc.layers.add(curve.layer, color=4 if curve.layer=="STIFFENERS" else 7)
        if curve.layer=="CUT_PLANE":
            if "CUT_LINE" not in doc.linetypes:
                doc.linetypes.new("CUT_LINE",dxfattribs={"description":"Cutting plane","pattern":[40,24,-6,4,-6]})
            doc.layers.get(curve.layer).dxf.linetype = "CUT_LINE"
            doc.layers.get(curve.layer).dxf.color = 4
        if len(pts) < 2:
            continue
        msp.add_lwpolyline(pts, close=curve.closed, dxfattribs={"layer":curve.layer})
        if curve.layer=="CUT_PLANE" and curve.label:
            for point in (pts[0],pts[-1]):
                msp.add_text(curve.label,height=height,dxfattribs={"layer":"ANNOTATION"}).set_placement(point)
            continue
        if curve.label and curve.label not in labels:
            key = f"R{len(labels)+1}"
            labels[curve.label] = key
            msp.add_text(key, height=height,dxfattribs={"layer":"ANNOTATION"}).set_placement(pts[len(pts)//2])
    dimensioned = view.projection in {"front","right","plan"} or plate
    if dimensioned:
        doc.layers.add("DIMENSIONS",color=7)
        a,b = low*1000,high*1000
        style = {"dimtxt":height, "dimasz":height*.65, "dimgap":height*.3, "dimdec":2}
        for ang,p0,p1,base in ((0,tuple(a),(b[0],a[1]),(a[0],a[1]-3*height)),
                               (90,(b[0],a[1]),tuple(b),(b[0]+3*height,a[1]))):
            if np.linalg.norm(np.subtract(p0,p1))>1e-8:
                msp.add_linear_dim(base=base,p1=p0,p2=p1,angle=ang,dimstyle="Standard",override=style,
                                   dxfattribs={"layer":"DIMENSIONS"}).render()
    annotations = (view.title,*view.notes,*legend,*(f"{n}    {label}" for label,n in labels.items()))
    for i, line in enumerate(annotations):
        msp.add_text(line, height=height, dxfattribs={"layer":"ANNOTATION"}).set_placement(
            (float(low[0])*1000, float(low[1])*1000 - height*((6 if dimensioned else 2)+1.6*i)))
    doc.saveas(path)
