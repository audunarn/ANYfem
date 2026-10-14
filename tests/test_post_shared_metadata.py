"""Focused summary-prefetch adapter coverage; existing dirty tests stay untouched."""
from concurrent.futures import ThreadPoolExecutor
from queue import SimpleQueue
from threading import get_ident
from types import SimpleNamespace,ModuleType
import sys
import numpy as np
import pytest
from anyfem.post.archive import ArchiveSession,PreparedArchive


def fixture(path,*,pooled):
    from anypost import write_results
    values=list(range(300)) if pooled else [9,1,9]
    cases=[dict(case_id=f"c{i}",status="completed",certified=True,route="stored",reaction_node_ids=values,diagnostics=[{"original":[i]}]) for i in range(3)]
    capsule=dict(schema="anysolver.recovery_capsule",version=1,runtime="anysolver-results/1",model_identity="consumer-model",mesh_identity="consumer-mesh",solver_identity={},geometry={"positions":np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]]),"triangles":np.array([[0,1,2]]),"element_ids":np.array([101])},patterns=(),coefficients=np.zeros((3,0)),cases=cases,descriptors=(),associations={},responses={},stored={},derived={})
    write_results(path,capsule);return cases


@pytest.mark.parametrize("pooled",[False,True])
def test_prepare_on_worker_preserves_explicit_detailed_api_and_bounded_listing(tmp_path,pooled):
    from any3dview import MeshHandle
    path=tmp_path/"metadata.h5";original=fixture(path,pooled=pooled)
    owner=get_ident();reads=[]
    with ThreadPoolExecutor(max_workers=1) as pool:prepared=pool.submit(ArchiveSession.prepare,path).result()
    reader=prepared._reader
    assert prepared._cases_are_summaries is pooled
    detail=reader.cases
    reader.cases=lambda:(reads.append(get_ident()),detail())[1]
    for name in ("metadata","metadata_summary","geometry","components"):
        setattr(reader,name,lambda *_:pytest.fail("owner startup must use prefetched snapshots"))
    viewer=SimpleNamespace(submit_update=SimpleQueue().put,add_mesh_arrays=lambda mesh:MeshHandle(mesh))
    with ArchiveSession(prepared,viewer) as session:
        assert not reads
        assert all("reaction_node_ids" not in record for record in session.case_summaries())
        assert list(session.cases())==original
        assert reads==([owner] if pooled else [])
        one=session.case_metadata("c1");assert one==original[1]
        one["reaction_node_ids"][0]=999
        assert session.case_metadata("c1")==original[1]
    with pytest.raises(RuntimeError):session.case_summaries()


def test_old_provider_metadata_and_prepared_positional_signature_remain_compatible(monkeypatch):
    values={"model_identity":"m","mesh_identity":"mesh","cases":[{"case_id":"a","status":"failed","certified":False,"route":"failed","reaction_node_ids":[9,3,9]}],"descriptors":[]}
    reader=SimpleNamespace(metadata=lambda:values,geometry=lambda:{},cases=lambda:values["cases"],components=lambda:[],close=lambda:None)
    package=ModuleType("anypost");package.open_results=lambda *_args,**_kw:reader
    presentation=ModuleType("anypost.presentation")
    class View:
        def __init__(self,*args,**kw):self.handle=object()
        def close(self):pass
    presentation.RetainedResultView=View
    monkeypatch.setitem(sys.modules,"anypost",package);monkeypatch.setitem(sys.modules,"anypost.presentation",presentation)
    prepared=ArchiveSession.prepare("legacy-provider")
    assert not prepared._cases_are_summaries
    assert PreparedArchive(reader,values,{},{})._cases_are_summaries is False
    viewer=SimpleNamespace(add_mesh_arrays=lambda _:None,submit_update=lambda _:None)
    with ArchiveSession(prepared,viewer) as session:
        assert session.cases()[0]["reaction_node_ids"]==[9,3,9]
        assert session.case_summaries()[0]=={"case_id":"a","status":"failed","certified":False,"route":"failed"}
        assert session.case_metadata("a")["reaction_node_ids"]==[9,3,9]


def test_pooled_cli_listing_is_summary_only(tmp_path,monkeypatch,capsys):
    from anypost.shared_metadata import CaseMetadataReader
    from anyfem.post.archive import main
    path=tmp_path/"cli.h5";fixture(path,pooled=True)
    monkeypatch.setattr(CaseMetadataReader,"_read_table",lambda *_:pytest.fail("CLI listing expanded shared IDs"))
    assert main([str(path),"--list"])==0
    result=__import__("json").loads(capsys.readouterr().out)
    assert result["metadata"]["cases_complete"] is False and len(result["cases"])==3
    assert "reaction_node_ids" not in result["cases"][0]
