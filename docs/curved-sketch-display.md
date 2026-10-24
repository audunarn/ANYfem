# Curved sketch display

Finished spline extrusions retain their exact geometry while the viewport draws an adaptive display approximation. Curved sketch and extrusion features no longer inherit the coarse two-division limit used by collapsed generators. The display samples the public face evaluator and refines each parameter direction independently, avoiding redundant rows in the straight extrusion direction. Display refinement is bounded and does not change engineering geometry or meshing tolerances.

Actual face boundaries remain visible; internal display subdivisions are grouped out of normal and selected outlines. Finite element mesh boundaries keep their existing behavior. Hiding a face and switching to the temporary camera scene retain matching ownership/group metadata.

While drawing or dragging a sketch, the viewport keeps the accepted model submission and replaces only temporary annotations. Finishing/cancelling, an external canvas clear, or changing renderer invalidates that temporary lifetime normally. These optimizations do not alter saved sketches, selection IDs, constraints, or undo.

Construction also reuses immutable, validated spline definitions and a bounded
set of evaluations at exactly repeated parameters. Parameters are not rounded
or approximated. Geometry validation, numerical tolerances and display
resolution remain unchanged; copied or reopened curves rebuild their derived
data from the saved definition.
