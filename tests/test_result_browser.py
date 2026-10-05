from types import SimpleNamespace
import pytest

from anyfem.model.records import ResultQuantityDescriptor
from anyfem.presentation.result_browser import result_entries


class MetadataDataset:
    field_keys=("stress_tensor","strain_tensor","displacement","energy")
    history_keys=("load_displacement",);table_keys=("stress_tensor_element_ids","strain_tensor_element_ids","displacement_node_ids")
    def field(self,key):
        family=key.split("_")[0]
        desc=ResultQuantityDescriptor(key,family.capitalize(),"global" if key=="energy" else "node" if key=="displacement" else "element",
            "Pa" if family=="stress" else "m" if family=="displacement" else "1",
            ("ux","uy","uz") if family=="displacement" else ("xx","yy","xy") if family in {"stress","strain"} else ())
        def read(*_):raise AssertionError("catalog read a field array")
        return SimpleNamespace(descriptor=desc,read=read)


def test_catalog_groups_every_advertised_component_without_reading_arrays():
    entries=result_entries(dataset=MetadataDataset())
    for family,key in (("Stress","stress_tensor"),("Strain","strain_tensor"),("Displacement","displacement")):
        found=[entry for entry in entries if entry.key==key]
        assert found and all(entry.path[1]==family for entry in found)
        assert all(entry.unit for entry in found)
        assert len([entry for entry in found if entry.component is not None])==3
    assert not any(entry.kind=="field" and entry.key=="energy" for entry in entries)
    assert not any(entry.label=="Component norm" and entry.key=="energy" for entry in entries)
    assert not any("Von Mises"==entry.label for entry in entries)
    assert any(entry.kind=="history" for entry in entries)


def test_live_field_catalog_preserves_names_and_does_not_invent_strain():
    fields=("magnitude","ux","uy","uz","rx","von_mises","membrane_xx","bending_yy","top_xy","bottom_xx","axial_stress")
    entries=result_entries(live_fields=fields)
    assert {entry.key for entry in entries}==set(fields)
    assert next(entry for entry in entries if entry.key=="membrane_xx").path==("Live fields","Stress","Membrane")
    assert next(entry for entry in entries if entry.key=="axial_stress").path[-1]=="Beam"
    assert not any("Strain" in entry.path for entry in entries)
    assert result_entries(live_fields=("equivalent_plastic_strain",))[0].path[-1]=="Strain"


def test_unknown_saved_component_refuses_before_reading_or_rendering():
    from anyfem.presentation.scene import build_persisted_result_scene
    with pytest.raises(ValueError,match="no saved component"):
        build_persisted_result_scene(None,None,MetadataDataset(),"stress_tensor",component="not_present")


def test_owner_live_quantity_descriptors_keep_every_advertised_component():
    from anysolver import ResultQuantity
    descriptor=ResultQuantity("strain.tensor","Strain tensor","element",("xx","yy","xy"),"1")
    entries=result_entries(live_quantities=(descriptor,))
    assert {entry.component for entry in entries}=={None,"xx","yy","xy"}
    assert all(entry.path[:2]==("Live quantities","Strain") for entry in entries)
