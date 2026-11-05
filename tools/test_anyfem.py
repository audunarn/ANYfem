"""Bounded development checks; full acceptance remains an explicit separate scope."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
QUICK = (
    "tests/test_license_policy.py", "tests/test_application_frontend_boundary.py",
    "tests/test_output_requests.py", "tests/test_command_recording.py",
    "tests/test_document_geometry_snapshot.py", "tests/test_selection_state.py",
    "tests/test_test_runner.py",
)


def selectors(profile, requested):
    if profile == "focused":
        if not requested:
            raise ValueError("focused requires explicit test files or node IDs")
        if any(not value.startswith("tests/") or "\\" in value or
               ".." in value.split("::", 1)[0].split("/") for value in requested):
            raise ValueError("focused selectors must be repository tests/ paths or node IDs")
        return list(requested)
    if requested:
        raise ValueError("selectors are allowed only with --profile focused")
    if profile == "quick":
        return list(QUICK)
    if profile == "qt":
        return ["tests/test_qt_workbench.py", "tests/test_qt_geometry_actions.py",
                "tests/test_qt_visibility.py", "tests/test_visibility.py",
                "tests/test_run_progress.py", "tests/test_qt_run_progress.py",
                "tests/test_qt_button_help.py", "tests/test_qt_mesh_parallel_selection.py",
                "tests/test_qt_result_browser.py", "tests/test_qt_activation_readiness.py",
                "tests/test_result_browser.py",
                "tests/test_geometry_sets_actions.py", "tests/test_application_frontend_boundary.py"]
    return ["tests"]


def owner_capabilities():
    missing = []
    for name, symbols in {
        "anygeometry": ("IntersectionBatchPolicy", "plan_intersections", "apply_intersections"),
        "anymesher": ("MeshAutomationOptions", "MeshRecoveryIncomplete"),
    }.items():
        module = importlib.import_module(name)  # A broken installed owner must fail.
        missing.extend(f"{name}.{symbol}" for symbol in symbols if not callable(getattr(module, symbol, None)))
    if missing:
        raise RuntimeError("required owner capabilities unavailable: " + ", ".join(missing))


def validate_verification(path, expected):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("verification evidence must be an object")
    results = data.get("results", [])
    if not isinstance(results, list) or any(not isinstance(row, dict) for row in results):
        raise RuntimeError("verification results must be a list of objects")
    identifiers = [row.get("case_id") for row in results]
    if any(not isinstance(identifier, str) for identifier in identifiers):
        raise RuntimeError("verification case IDs must be strings")
    if not expected or len(identifiers) != len(expected) or set(identifiers) != set(expected):
        raise RuntimeError("verification evidence is missing, duplicated or incomplete")
    if data.get("status") != "passed" or any(row.get("status") != "passed" for row in results):
        raise RuntimeError("verification evidence records failure")
    if data.get("counts") != {"total": len(expected), "passed": len(expected), "failed": 0}:
        raise RuntimeError("verification counts do not match the complete case inventory")


_PROCESS_OWNER = None


def _process_owner():
    global _PROCESS_OWNER
    if _PROCESS_OWNER is None:
        spec = importlib.util.spec_from_file_location(
            "_anyfem_test_process_owner", ROOT / "tools/_owned_test_process.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _PROCESS_OWNER = module
    return _PROCESS_OWNER


def start_owned_process(command, **options):
    if os.name == "nt":
        return _process_owner().start_windows_process(command, **options)
    options.pop("gate_root", None)
    return subprocess.Popen(command, **options)


def stop_owned_process(process, windows=None):
    """Stop the owned pytest tree, including scientific subprocesses."""
    windows = os.name == "nt" if windows is None else windows
    if windows:
        if not isinstance(process, _process_owner().WindowsOwnedProcess):
            raise RuntimeError("Windows pytest cleanup requires a retained owned Job")
        process.stop()
        return
    try:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if not windows:
                os.killpg(process.pid, signal.SIGKILL)
            process.kill()
            process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError) as error:
        # Never claim interrupted work stopped if tree cleanup could not be confirmed.
        process.kill()
        raise RuntimeError("could not confirm stopping the owned pytest process tree") from error
    finally:
        if not windows:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def execute(command, log, environment):
    with log.open("w", encoding="utf-8") as stream:
        process = start_owned_process(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                   start_new_session=os.name != "nt", gate_root=log.parent)
        owned_windows = (os.name == "nt"
                         and isinstance(process, _process_owner().WindowsOwnedProcess))
        output = _process_owner().owned_output(process) if owned_windows else process.stdout
        try:
            if owned_windows:
                log.with_name("pytest-process.json").write_text(json.dumps({
                    "logical_command": command,
                    "bootstrap_command": process.bootstrap_command,
                    "ownership": "retained-kill-on-close-job-before-go",
                    "bootstrap_pid": process.pid,
                }, indent=2), encoding="utf-8")
            for line in output:
                stream.write(line)
                stream.flush()
                if environment.get("ANYFEM_CI_DIAGNOSTICS_DIR") or "%]" in line:
                    print(line.strip(), flush=True)
            return process.wait()
        except KeyboardInterrupt:
            stop_owned_process(process)
            raise
        finally:
            try:
                if owned_windows:
                    stop_owned_process(process)
            finally:
                if owned_windows:
                    _process_owner().close_owned_output(process, output)
                else:
                    process.stdout.close()


def _caller_profile():
    spec=importlib.util.spec_from_file_location('_anyfem_caller_profile',ROOT/'tools/ci_caller_planning_profile.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def caller_phase_assignments(inventory):
    """Partition the actual complete collection, preserving each original ID once."""
    required=_caller_profile().REQUIRED_CASES
    selected={key:[] for key in required}
    for node in inventory:
        file,_,name=node.partition('::')
        key=Path(file).stem+'::'+name
        if key in selected:selected[key].append(node)
    if any(len(nodes)!=1 for nodes in selected.values()):
        raise RuntimeError('missing or ambiguous required caller case')
    positives=[selected[key][0] for key in sorted(required)]
    return [[node for node in inventory if node not in positives],*([node] for node in positives)]


def _collection_skip_key(case):
    skip=case.find('skipped')
    if skip is None or any(case.find(kind) is not None for kind in ('failure','error')):
        raise RuntimeError('unknown collection result')
    return (case.get('classname',''),case.get('name',''),tuple(sorted(skip.attrib.items())),skip.text or '')


def merge_caller_junit(paths,target,inventory,collection_skips=()):
    """Merge only actual rows, requiring the complete node and collection-skip union."""
    seen=set();seen_skips=set();expected_skips={tuple_key for tuple_key in collection_skips}
    if len(expected_skips)!=len(collection_skips):raise RuntimeError('duplicate collection skip inventory')
    root=ET.Element('testsuites')
    for path in paths:
        tree=ET.parse(path).getroot()
        suites=[tree] if tree.tag=='testsuite' else list(tree.findall('testsuite'))
        if not suites:raise RuntimeError('missing pytest suites')
        for suite in suites:
            for case in suite.findall('testcase'):
                if any(case.find(kind) is not None for kind in ('failure','error')):
                    raise RuntimeError('ordinary test failure in full caller union')
                nodes=[prop.get('value') for prop in case.findall('properties/property') if prop.get('name')=='anyfem_original_nodeid']
                if not nodes:
                    key=_collection_skip_key(case)
                    if key not in expected_skips or key in seen_skips:raise RuntimeError('unknown or duplicate collection skip')
                    seen_skips.add(key);continue
                if len(nodes)!=1 or nodes[0] not in inventory or nodes[0] in seen:
                    raise RuntimeError('missing, unknown or duplicate original node identity')
                node=nodes[0]
                if [case.get('classname',''),case.get('name','')]!=inventory[node]:
                    raise RuntimeError('original JUnit identity differs')
                seen.add(node)
            root.append(suite)
    if seen!=set(inventory) or seen_skips!=expected_skips:
        raise RuntimeError('incomplete original full collection union')
    _caller_profile().verify_junit(paths)
    ET.ElementTree(root).write(target,encoding='utf-8',xml_declaration=True)


def _verify_caller_positive_phase(path,node,identity):
    cases=list(ET.parse(path).iter('testcase'))
    if len(cases)!=1:raise RuntimeError('isolated caller phase must emit exactly one case')
    case=cases[0]
    ids=[p.get('value') for p in case.findall('properties/property') if p.get('name')=='anyfem_original_nodeid']
    if ids!=[node] or [case.get('classname',''),case.get('name','')]!=identity:
        raise RuntimeError('isolated caller original identity differs')
    file,_,name=node.partition('::')
    if _caller_profile().validate_case(case)!=Path(file).stem+'::'+name:
        raise RuntimeError('isolated caller positive key differs')


def _caller_bootstrap(path):
    """Private fresh interpreter entry; fixtures and bodies remain ordinary pytest."""
    sys.path.insert(0,str(ROOT))  # Same checkout import root as python -m pytest.
    import pytest
    from _pytest.junitxml import mangle_test_address
    path=Path(path);data=json.loads(path.read_text(encoding='utf-8'))
    kind=data['kind'];inventory=data.get('inventory',{});assigned=data.get('assigned',[])
    if kind not in ('collect','core','positive'):raise RuntimeError('unknown caller phase')
    class ExactCollection:
        @pytest.hookimpl(trylast=True)
        def pytest_collection_modifyitems(self,session,config,items):
            ids=[item.nodeid for item in items]
            if len(ids)!=len(set(ids)):raise RuntimeError('duplicate collected node')
            if kind!='collect':
                expected=list(inventory) if kind=='core' else assigned
                if set(ids)!=set(expected):raise RuntimeError('full collected node universe differs')
                chosen=set(assigned);removed=[item for item in items if item.nodeid not in chosen]
                by_id={item.nodeid:item for item in items}
                if removed:config.hook.pytest_deselected(items=removed)
                items[:]=[by_id[node] for node in assigned]
                if len(items)!=len(assigned):raise RuntimeError('assigned count differs')
            for item in items:item.user_properties.append(('anyfem_original_nodeid',item.nodeid))
        def pytest_collection_finish(self,session):
            if kind=='collect':
                collected={}
                for item in session.items:
                    names=mangle_test_address(item.nodeid)
                    collected[item.nodeid]=['.'.join(names[:-1]),names[-1]]
                data['inventory']=collected
                path.write_text(json.dumps(data,indent=2),encoding='utf-8')
    owner_capabilities()
    if kind=='positive':_caller_profile().preflight(None)
    args=list(data['args'])
    if kind=='collect':args.append('--collect-only')
    return pytest.main(args,plugins=[ExactCollection()])


def _execute_caller_phases(command,output,environment,report):
    """One original collection; core plus four fresh Hs, under the existing job cap."""
    (output/'pytest.log').write_text('Fresh-H caller phases: see each phase/pytest.log; no renewed job allowance.\n',encoding='utf-8')
    started=time.monotonic();report['caller_host_scope']='fresh-H-per-required-positive; fixture caches unchanged'
    report['phases']=[];paths=[]
    args=[arg for arg in command[3:] if not arg.startswith('--junitxml=')]
    def phase(name,data):
        folder=output/name;folder.mkdir(exist_ok=False)
        data['args']=[*data['args'],'--junitxml='+str(folder/'pytest.xml')]
        manifest=folder/'phase.json';manifest.write_text(json.dumps(data,indent=2),encoding='utf-8')
        env=environment.copy()
        if 'ANYFEM_CI_DIAGNOSTICS_DIR' in env:env['ANYFEM_CI_DIAGNOSTICS_DIR']=str(folder)
        now=time.monotonic()
        code=execute([sys.executable,str(Path(__file__).resolve()),'--_caller-bootstrap',str(manifest)],folder/'pytest.log',env)
        row=dict(phase=name,exit_code=code,phase_seconds=time.monotonic()-now,
                 cumulative_driver_seconds=time.monotonic()-started,scope='existing whole-job cap; no renewed allowance')
        report['phases'].append(row)
        with (output/'pytest.log').open('a',encoding='utf-8') as stream:stream.write(json.dumps(row)+'\n')
        (output/'caller-phases.json').write_text(json.dumps(report['phases'],indent=2),encoding='utf-8')
        return code,manifest,folder/'pytest.xml'
    code,manifest,xml=phase('caller-collection',dict(kind='collect',args=args))
    if code:return code
    data=json.loads(manifest.read_text(encoding='utf-8'));inventory=data['inventory']
    if not inventory:raise RuntimeError('empty full collection')
    collection_skips=[_collection_skip_key(case) for case in ET.parse(xml).iter('testcase')]
    assignments=caller_phase_assignments(inventory)
    report['original_collection_items']=len(inventory);report['original_collection_skips']=len(collection_skips)
    for i,assigned in enumerate(assignments):
        if i==0 and not assigned:continue
        if i==0:phase_args=args
        else:
            # The original full selector is tests; all other pytest options stay exact.
            if args[0]!='tests':raise RuntimeError('unexpected full profile selector')
            phase_args=[*assigned,*args[1:]]
        code,_,xml=phase('caller-core' if i==0 else 'caller-positive-'+str(i),
                        dict(kind='core' if i==0 else 'positive',args=phase_args,
                             inventory=inventory if i==0 else {node:inventory[node] for node in assigned},assigned=assigned))
        if code:return code
        if i:_verify_caller_positive_phase(xml,assigned[0],inventory[assigned[0]])
        paths.append(xml)
    merge_caller_junit(paths,output/'pytest.xml',inventory,collection_skips)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("quick", "focused", "qt", "full"), default="quick")
    parser.add_argument("--backend", choices=("software", "gpu"), default="software")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ci-diagnostics", action="store_true",
                        help="stream named pytest phases and failures; retain a live journal")
    parser.add_argument("--fail-fast", action="store_true",
                        help="stop pytest after its first failure; never establish partial acceptance")
    parser.add_argument("--out", type=Path, help="new evidence directory; existing paths are refused")
    parser.add_argument("tests", nargs="*")
    options = parser.parse_args(argv)
    try:
        selected = selectors(options.profile, options.tests)
    except ValueError as error:
        parser.error(str(error))
    command = [sys.executable, "-m", "pytest", *selected, "-q"]
    if options.ci_diagnostics:
        command += ["-p", "tools.pytest_anyfem_progress", "-o", "anyfem_snapshot_timeout=60"]
    if options.fail_fast:
        command += ["--maxfail=1"]
    if options.dry_run:
        print(json.dumps({"profile": options.profile, "command": command,
                          "acceptance": "not established"}, indent=2))
        return 0
    output = options.out or ROOT / "reports" / "tests" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = output.resolve()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        parser.error(str(error))
    environment = os.environ.copy()
    # An inherited -k/--ignore/--collect-only must not silently narrow a profile.
    inherited_options = environment.pop("PYTEST_ADDOPTS", None)
    # This private child setting cannot enable diagnostics without the flag.
    environment.pop("ANYFEM_CI_DIAGNOSTICS_DIR", None)
    if options.ci_diagnostics:
        environment["ANYFEM_CI_DIAGNOSTICS_DIR"] = str(output)
    if options.profile == "qt":
        environment["ANYFEM_RUN_QT_TESTS"] = "1"
        environment["ANYFEM_QT_TEST_BACKEND"] = options.backend
    if options.profile == "full":
        environment["ANYFEM_VERIFICATION_OUT"] = str(output / "verification")
    command += ["--junitxml=" + str(output / "pytest.xml")]
    report = {"profile": options.profile, "command": command, "python": sys.executable,
              "ci_diagnostics": options.ci_diagnostics, "fail_fast": options.fail_fast,
              "python_version": sys.version, "ignored_pytest_addopts": inherited_options,
              "environment": {key: environment.get(key) for key in (
                  "ANYFEM_RUN_QT_TESTS", "ANYFEM_QT_TEST_BACKEND", "ANYFEM_RUN_GUI_TESTS",
                  "ANYFEM_RUN_SCALE_GATES", "ANYFEM_RUN_HARDWARE_GATES", "ANYFEM_TEST_THREADS")},
              "acceptance": "not established", "status": "running"}
    start = time.monotonic()
    code = 2
    try:
        if options.profile in {"full", "qt"}:
            owner_capabilities()
        if options.profile == "qt":
            importlib.import_module("PySide6.QtWidgets")
        if options.profile == "full" and environment.get("ANYGEOMETRY_REQUIRE_QUALIFIED_PLANNING_TESTS")=="1":
            code = _execute_caller_phases(command, output, environment, report)
        else:
            code = execute(command, output / "pytest.log", environment)
        report["status"] = "passed" if code == 0 else "failed"
        if (output / "pytest.xml").exists():
            suites = ET.parse(output / "pytest.xml").getroot().findall("testsuite")
            report["counts"] = {key: sum(int(suite.get(key, "0")) for suite in suites)
                                for key in ("tests", "failures", "errors", "skipped")}
        if code == 0:
            counts = report.get("counts", {})
            if counts.get("tests", 0) <= counts.get("skipped", 0):
                raise RuntimeError("no executed tests were recorded")
            if options.profile == "qt":
                rows = ET.parse(output / "pytest.xml").getroot().iter("testcase")
                if not any("test_qt_workbench" in row.get("classname", "") and
                           row.find("skipped") is None for row in rows):
                    raise RuntimeError("no Qt workbench tests executed")
        if code == 0 and options.profile == "full":
            from anyfem.verification import cases
            validate_verification(output / "verification" / "verification.json", {case.case_id for case in cases()})
    except KeyboardInterrupt:
        report["status"] = "interrupted"
        code = 130
    except (ImportError, RuntimeError, OSError, ValueError, ET.ParseError) as error:
        report["status"] = "blocked" if not (output / "pytest.log").exists() else "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        code = 2
    finally:
        report.update(exit_code=code, seconds=time.monotonic() - start)
        (output / "run.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report.get("counts", {})), flush=True)
        print(f"{report['status']}: {output / 'run.json'}", flush=True)
    return code


if __name__ == "__main__":
    if sys.argv[1:2]==['--_caller-bootstrap']:
        raise SystemExit(_caller_bootstrap(sys.argv[2]))
    raise SystemExit(main())
