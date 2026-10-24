# Apply tree properties to selected geometry

Select geometry in the canvas first. Right-click a **Plate sections** or **Beam sections** row, then choose **Apply to selection**. The canvas selection stays highlighted while the menu is open. Plate sections apply to selected plates; beam sections apply to selected lines/beam axes. Other selected entity kinds are skipped. Unselected members of a generated feature are not included.

The **Mesh sizes** tree branch lists existing local refinement policies with their size, radius and growth. Right-click one and choose **Apply to selection** to reuse it on selected geometry points, lines or plates. A policy defined around a coordinate is cloned onto each selected geometry entity; its size, radius, growth and name remain unchanged. Choose **Define mesh sizes in Mesh…** to open the existing mesh controls.

Each Apply operation is one undo step. Undo restores both assignments and structural ownership; redo and save/reopen preserve the result. If no compatible geometry is selected, Apply is disabled with an explanation. A menu opened before the document, geometry or chosen definition changes must be opened again. Read-only projects cannot apply properties.
