# Model colour modes

The camera toolbar offers **Thickness**, **Section**, **Density** and **Default**. These change only displayed model/mesh colours. The selected colour mode stays active when switching between Model and Mesh. Default restores the ordinary colours. Results and diagnostic mesh inspection keep their own colours.

- Thickness groups plates by their actual SI thickness. Equal thicknesses share a colour even when section names differ. The legend uses the project's length units.
- Section gives each stable beam-section definition a deterministic colour, including both its axis and displayed profile. Grey identifies unassigned entities.
- Density in Model displays the mesher's requested target size, sampled on displayed geometry using the project's resolved local refinement controls. Its legend says **Target mesh size**. If no global target is stored, or a refinement cannot be resolved, the legend explains why the target is unavailable.
- Density in Mesh displays **Actual element size**: the square root of the shell corner-polygon area or the end-to-end beam length. This is a characteristic size, not a quality score or a guarantee of meshing tolerance. Imported meshes use their elements and any available section-owner information; no geometry is inferred.

The legend is bounded to the canvas. Large category lists are shortened on the canvas; the active colour button's tooltip contains the full category key. Show legend, legend width and font settings also apply. Colour modes do not edit sections, mesh controls, selection, geometry, saved project data or result field colour maps.

Density legends use evenly spaced range levels and four significant figures. Numerically different sizes that share the same displayed range endpoint value use a single display colour; this changes only the colour scale.
