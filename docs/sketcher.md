# Sketcher

Start a sketch from Geometry, or open an existing sketch from the model tree.
Choose a construction plane or a flat plate face. New sketches start with
Connected lines; existing sketches open with Select.

## Draw and edit

- **Select:** click a point or segment. Hold Shift to select several. Drag a
  point to move it within its plane; existing constraints remain active.
- **Line:** click the start and end. Clicking an existing endpoint reuses it.
- **Connected lines:** click successive points. Enter completes the chain;
  Close loop joins it to its first point.
- **Arc:** click the start, a point on the arc, and the end.
- **Circle:** click the centre and a point on its circumference.
- **Spline:** choose Fit-point spline or Control-point NURBS. Click the points,
  then press Enter. Closed profile creates a closed curve. Degree and optional
  control-point weights are available under Advanced.
- **Rounded corner:** Shift-select two straight segments meeting at a corner,
  enter the radius, then apply it.

Enter accepts the active numeric field first, then completes the drawing tool,
then finishes the sketch. Escape abandons the pending tool or drag and returns
to Select. Cancel discards the whole working sketch. Undo sketch and Redo sketch
operate within the sketch; after Finish sketch, document undo restores the
previous saved model.

## Dimensions and constraints

Select two points to set their distance in Dimensions and constraints. Select
an arc or circle to edit its radius or diameter. Use project units or explicit
units such as `250 mm`. Dimensions can also be selected in the canvas.

Coincident, Horizontal, Vertical and Fixed constrain the selected geometry.
Constraints are listed individually and can be removed. A fixed point cannot
be dragged. An inconsistent edit keeps the last valid sketch and reports the
problem; remaining degrees of freedom describe how much movement is available.

## Cut a plate

Finish the sketch, then use Cut plate, the sketch's model-tree context menu,
or Edit → Cut plate with sketch. Choose the target plate and the closed
contours to use. Preview opening validates and displays the proposed cut
without changing the project. Cut plate saves it as a linked feature.

Editing the source sketch updates its cuts. Existing holes and plate section
assignments are retained. Remove dependent cuts before deleting or suppressing
their source sketch. A successful geometry edit invalidates the existing mesh.

Cut contours must lie strictly inside a coplanar, flat plate. Open,
self-intersecting, touching and nested contours are refused. Separated circular
and spline holes can coexist. A contour combining arcs and NURBS, or mixed
contours with overlapping conservative bounds, is currently refused when its
validity cannot be certified. Display sampling is never used to accept an
uncertain cut. Spline extrusion is currently unavailable; profile editing and
plate cuts remain available.
