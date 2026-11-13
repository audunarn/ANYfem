# Plate drawings

Open **Examples → Support B — plate drawings…** to load an editable copy of the 11-plate assembly. It opens the **Drawings** workspace beside **Submodelling**, with four assemblies and a nominal steel density of 7,850 kg/m³. The expected net mass is 199.28 kg per assembly. Choose **Drawings** directly to work on the current model.

| Source | How to use it |
|---|---|
| **Assembly** | Select a structural Part, quantity, weight basis and elevation datum. **Support lower edge · Z = 0** shifts the assembly to its lowest material edge; **Model global Z** preserves source elevations in the views and plate index. Generate the preview and use the view selector to inspect the isometric, plan, front and right views. The first printed sheet is the isometric. Export includes orthographic views, individual plate profiles, the index and net weights by gauge. |
| **Plate** | Click a plate in the viewport. The preview follows the picked face to its physical Sheet. Use **Include attached stiffeners** for member axes and identification. **Show intersecting plates** adds dashed contact references with the other plate marks and thicknesses. Cylindrical Sheets open into a development at the reference radius. The drawing states its radius and the cut used for a closed circumference. An unassigned face can provide an outline before stock has been specified. |
| **Plane** | Select a principal plane through the model centre, an active or named workplane, a planar face, or a custom origin and normal. Set the normal offset in mm. Enable **Limit section to a rectangle** and enter the U and V limits in mm. U is across the view and V is up. The export records the actual plane and limits. |

To add a detail, preview a plate or plane section, enter a sheet name and choose **Add sheet**. Return to **Assembly** and generate its preview to issue the views together. The queue belongs to the current document revision. A geometry edit clears it. Saved plate and plane sheets retain their reviewed annotation settings. Changing the elevation datum clears queued assembly views so they can be regenerated consistently. Export to a new directory to keep previous issues intact.

DXF coordinates are true-size millimetres. A3 PDFs fit the view to the sheet. **DWG + DXF + PDF** uses OpenCAD Studio to write AutoCAD 2018 DWGs. **Open last export in OpenCAD Studio** opens the exported view. If ANYfem cannot find the application, the pane displays a notice and a **Locate…** button for portable installations. DXF and PDF export remain available, and you can select ODA File Converter for DWG output.

## Headless plate index and profiles

The existing **File → Create plate drawings…** command and CLI export individual planar profiles and the stock takeoff. Each named Sheet supplies one physical plate mark. Use **Join Sheet** for faces that belong to one physical plate. Distinct fabricated plates retain separate Sheets even when they share a gauge and plane.

Install the optional drawing dependencies with `pip install "ANYfem[drawings]"`. The command line exports the same package from a saved project.

```shell
anyfem-plate-drawings model.anyfem drawings --part 1 --quantity 4 --density 7850 --elevation-datum global
```

The part number is the persistent structural Part ID displayed in the drawing dialog. The command writes the following files.

- One 1:1 millimetre DXF per plate with separate outer-contour and hole layers. To include AutoCAD 2018 DWGs, select the ODA File Converter executable in the dialog or pass its path with `--dwg-converter`.
- A plate index giving the mark, Sheet ID, stock, net area and weight, order quantity, and lower and upper material elevations. Choose `--elevation-datum support_base` (the export default) for local Z = 0 at the lowest material edge, or `--elevation-datum global` to preserve model elevations. The thickness is centred on each native reference surface.
- A takeoff grouped by nominal thickness, with totals per assembly and for the requested quantity. Plate mass equals net area after openings × nominal thickness × density. The default uses each assigned material's density. A takeoff override affects only the export. Contingency is additional to these net values.
- A drawing-basis file identifying the project snapshot, Part, global elevation of the local datum, density basis, quantity and file formats.

The assembly takeoff covers planar plate-only Parts. The plate picker also handles cylindrical developments and modelled stiffeners. A developed cylindrical contour is a reference-surface profile. Forming allowance depends on the fabrication process. Plane sections represent assigned plate thickness, straight beam sections and supported cylindrical walls. The preview reports omitted geometry, including unsupported cylindrical trims or curved member sweeps.

## Plate intersection references

Plate previews and exported profile DXFs show finite contact footprints on a separate dashed `PLATE_INTERSECTIONS` layer. T-joints, crossings and contacts at the plate skins are clipped to the actual material, including openings. Equivalent internal seams are omitted. The cutting contours, hole geometry and net weights stay unchanged. Use `--no-plate-intersections` to omit these references from a CLI export.

Planar plate targets and axial or perpendicular planar contacts on cylindrical developments are supported. Oblique cylinder contacts, cylinder-to-cylinder contacts and unsupported trims are reported in the drawing notes. These references do not infer welds or establish a collision check. Physical plate ownership must be defined by Sheets; ambiguous ownership is refused.

For Python callers, `assembly_views` defaults to global coordinates for compatibility; profile-package export defaults to `support_base`. Pass `elevation_datum` explicitly to choose the same datum across both.

## SAT geometry

Choose **File → Import SAT geometry…** to create an editable project from an indexed ACIS SAT file. Surface pieces become individually selectable, unassigned Sheets. Join connected pieces belonging to one physical plate before assigning its material and section and generating a plate index or weight takeoff. Source coordinates convert to metres using the SAT units header. The saved project retains source identity, precision and topology maps.

Curved SAT import uses a local FreeCAD Python installation with InventorLoader. A missing reader or CAD adapter produces an actionable message. The supported reader covers planar and cylindrical faces, straight curves, analytic ellipse intervals and supported rational spline laws. Unsupported geometry produces entity diagnostics. Cancel or close the import to retain the current document.
