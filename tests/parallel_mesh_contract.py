"""Public-caller fixtures and numbering-independent mesh comparison."""
from __future__ import annotations

import numpy as np
from anyfem.model.project import Project
from anyfem.model.sections import BeamSection
from anymaterial import MaterialSpec


def separated_plates(count=4, *, beams=False):
    project = Project("parallel contract")
    project.add_material(MaterialSpec("steel", constants={"elastic_modulus": 210e9, "poisson_ratio": 0.3}))
    project.add_plate_section("plate", 0.01, "steel")
    if beams:
        project.add_beam_section(BeamSection(
            "stiffener", "Flatbar", "steel", flange_width=0.1,
            flange_thickness=0.01, eccentricity=0.05,
            web_direction=(0.0, 0.0, 1.0),
        ))
    for index in range(count):
        x, y = 10.0 * (index % 6), 10.0 * (index // 6)
        vertices = project.geometry.add_points(
            ((x, y, 0), (x + 2, y, 0), (x + 2, y + 1, 0), (x, y + 1, 0)))
        face = project.geometry.add_plate(vertices)
        project.assign_plate(face, "plate")
        if beams:
            project.assign_beam(project.geometry.faces[face].loop[0].edge, "stiffener")
    return project


def mesh_signature(mesh):
    # Same 1e-9 coordinate comparison as the mesher's existing parity oracle.
    # Preserve full connectivity and interpolation weights, not just centroids.
    def position(node):
        return tuple(np.round(mesh.nodes[node], 9))

    def connectivity(element):
        nodes = mesh.shells.get(element) or mesh.beams[element]
        return tuple(position(node) for node in nodes)

    def associations(mapping, function):
        return {key: sorted(function(item) for item in values)
                for key, values in mapping.items()}

    return {
        "nodes": sorted(position(node) for node in mesh.nodes),
        "quads": sorted(tuple(position(node) for node in nodes) for nodes in mesh.quads.values()),
        "tris": sorted(tuple(position(node) for node in nodes) for nodes in mesh.tris.values()),
        "beams": sorted(tuple(position(node) for node in nodes) for nodes in mesh.beams.values()),
        "faces": associations(mesh.elements_of_face, connectivity),
        "members": associations(mesh.elements_of_member, connectivity),
        "edges": associations(mesh.nodes_of_edge, position),
        "offset_edges": associations(mesh.offset_nodes_of_edge, position),
        "vertices": {key: position(node) for key, node in mesh.node_of_vertex.items()},
        "thickness": dict(mesh.thickness_of_face),
        "couplings": sorted((
            position(item.beam_node),
            tuple(sorted((position(node), round(weight, 12))
                         for node, weight in zip(item.plate_nodes, item.weights))),
            tuple(np.round(item.eccentricity, 9)),
        ) for item in mesh.couplings.values()),
    }
