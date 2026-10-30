# Measure geometry

Choose **Measure** beside **Visualization** in the workspace toolbar. Geometry → Inspect → Measure selected geometry opens the same workspace.

Choose a tool, select geometry in the model tree or canvas, then click **Measure selection**. Use Shift-click to add a second canvas entity. The pane shows selected entity IDs and instructions. Read-only projects are supported. Measurement creates no undo entry and does not edit the model or invalidate its mesh. Opening Measure returns to geometry picking while retaining the existing mesh/results.

| Tool | Selection and result |
| --- | --- |
| Auto | One point: coordinates; one line: length; one plate: area; two entities: distance |
| Distance | Two geometry entities; point-to-point distance or sampled minimum estimate for other entities |
| Angle | Two lines; unsigned angle between midpoint tangents, from 0 to 90 degrees, independent of direction |
| Coordinates | One point; signed world X, Y, Z |
| Length | One line |
| Area | One plate; numerical area |
| Perimeter | One plate; includes outer and hole boundaries |
| Radius | One circular arc; other curves are unsupported |
| Centroid | One entity; world X, Y, Z |
| Normal | One plate; dimensionless midpoint unit normal in world XYZ |

Results use current project units, including squared length units for area. Curved area and centroid values, and distances involving lines or plates, are sampling-based estimates. An angle is a midpoint tangent comparison rather than a general interior angle. Unsupported selections show a diagnostic without substituting another measurement.

Changing the selection, tool, model revision, project or units clears the previous result. Measure again for the current source. Mesh nodes and elements are not reinterpreted as geometry points.
