"""Headless integration entry; caller supplies admitted project/mesh dictionaries.

This helper computes and applies pressure history only. It does not launch a
structural solve or create/replace the caller's hull geometry.
"""
from dataclasses import asdict
from anyfem.application.irregular_wave_input import IrregularWaveInput
from anyfem.application.hydro_wave_input import compute_wave_input,ApplyWavePressure
from anyfem.io.project_file import project_to_dict
from anymesher.serialize import mesh_to_dict


def apply_irregular_pressure(project,mesh,*,cache_directory,settings=None):
    settings=IrregularWaveInput() if settings is None else settings
    result=compute_wave_input(document=project_to_dict(project),mesh_data=mesh_to_dict(mesh),
        settings=asdict(settings),source_bank_cache_directory=cache_directory)
    command=ApplyWavePressure(result)
    return command.do(project),command
