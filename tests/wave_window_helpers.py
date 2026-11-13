"""Shared non-test retained irregular-wave record for headless and Qt checks."""
from dataclasses import asdict, replace
import json
from test_pressure_remap import source_bank, project_and_meshes as barge_meshes
from anyfem.application import pressure_transfer as bridge
from anyfem.application.hydro_wave_pressure import target_hull_cells
from anyfem.application.irregular_wave_input import IrregularWaveInput
from anyfem.document import canonical_hash
from anymesher.serialize import mesh_to_dict
from anyloads.spectral_pressure_history import build_pressure_mapping_plan, map_pressure_bank, SpectralMappedPressureHistory


def irregular_record():
    project, old, new = barge_meshes()
    config = IrregularWaveInput(component_count=2, seed=22)
    realization = config.realization()
    bank = replace(source_bank(), omegas=tuple(c.omega for c in realization.components), bank_id='')
    cells = target_hull_cells(project, old, {s.name: s.ref.id for s in project.wet_surfaces})
    mesh_hash = canonical_hash(mesh_to_dict(old))
    plan = build_pressure_mapping_plan(bank, *cells, mesh_content_sha256=mesh_hash)
    fields, _ = map_pressure_bank(bank, plan, *cells[1:4], surface_ids=cells[4], mesh_content_sha256=mesh_hash)
    history = SpectralMappedPressureHistory(config.times, tuple(cells[0]), tuple(bank.omegas), fields.real, fields.imag,
        bank.heading, mesh_hash, bank.bank_id, json.dumps(realization.to_dict()), json.dumps(dict(settings=asdict(config))))
    history = bridge.retain_pressure_source(history, bank, realization, project)
    return project, old, new, config, history, None
