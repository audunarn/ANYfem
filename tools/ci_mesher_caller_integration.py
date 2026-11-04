"""Require all unchanged mesher/Project integration cases after relocation."""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

REQUIRED_CASES=frozenset(['test_mesher_authored_child_project_cells::test_changed_node_and_quadratic_request_refuse', 'test_mesher_authored_child_project_cells::test_crossing_or_wrong_child_refuses_even_inside_original_root', 'test_mesher_authored_child_project_cells::test_incomplete_duplicate_and_overlapping_child_partitions_refuse', 'test_mesher_authored_child_project_cells::test_missing_selected_child_boundary_stations_refuse', 'test_mesher_authored_child_project_cells::test_selected_left_cells_have_literal_owner_proof_without_mesh_admission', 'test_mesher_authored_child_project_cells::test_stale_project_during_private_publication_discards_stage', 'test_mesher_authored_project_references::test_changed_project_and_stale_owner_refuse_before_retention_check', 'test_mesher_authored_project_references::test_child_pressure_refuses_root_only_association', 'test_mesher_authored_project_references::test_private_stage_preflight_retains_root_source_and_rechecks_before_swap', 'test_mesher_authored_project_references::test_required_line_edge_must_be_a_registered_active_shell_chain', 'test_mesher_authored_project_references::test_root_only_input_scope_binds_exact_project_source'])


def verify_junit(paths):
    seen=set()
    for path in paths:
        for case in ET.parse(path).iter('testcase'):
            key=case.get('classname','').rsplit('.',1)[-1]+'::'+case.get('name','')
            if key not in REQUIRED_CASES:continue
            if key in seen:raise ValueError('duplicate relocated integration case: '+key)
            if any(case.find(kind) is not None for kind in ('failure','error','skipped')):
                raise ValueError('relocated integration case did not pass: '+key)
            seen.add(key)
    if seen!=REQUIRED_CASES:
        raise ValueError('missing relocated integration cases: '+', '.join(sorted(REQUIRED_CASES-seen)))
    return len(seen)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('junit',nargs='+',type=Path)
    args=parser.parse_args()
    print(json.dumps({'relocated_integration_cases_passed':verify_junit(args.junit)}))


if __name__=='__main__':main()
