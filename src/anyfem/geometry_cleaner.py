"""Project-safe geometry review, preview and undoable cleanup commands."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass

from anygeometry.cleaner import (
    CleanerOptions, CleanerReport, RepairPlan, apply_repair,
    propose_repair, review_geometry, validate_repair_plan,
)
from anygeometry.entities import EntityRef
from anygeometry.errors import GeometryError

from .commands import GeometryCommand


def diagnosis_category(finding) -> str:
    """Presentation categories preserve owner severity and unqualified coverage."""
    if finding.severity == 'blocker':
        if (finding.kind.startswith(('unsupported', 'unclassified', 'unverified'))
                or finding.kind in ('capability_missing', 'check_failed', 'diagnostic_failure')):
            return 'Coverage limitations'
        return 'Model blockers'
    return {'error': 'Model errors', 'warning': 'Review warnings',
            'info': 'Information'}.get(finding.severity, finding.severity)


def diagnosis_summary(report):
    """Summarize every raw finding without changing audit or export semantics."""
    from collections import Counter
    categories = Counter(diagnosis_category(item) for item in report.findings)
    grouped = Counter((diagnosis_category(item), item.kind) for item in report.findings)
    order = ('Model errors', 'Model blockers', 'Review warnings', 'Information', 'Coverage limitations')
    counts = '; '.join(f'{categories[name]} {name.lower()}' for name in order)
    status = 'Diagnosis complete.' if report.complete else 'Diagnosis incomplete; see coverage.'
    audit = 'Strict geometry audit passed.' if report.audit_clean else 'Strict geometry audit has not passed.'
    groups = []
    for category in order:
        for group_category, kind in sorted(grouped):
            if group_category != category:
                continue
            refs = {(ref.kind, ref.id) for item in report.findings
                    if item.kind == kind and diagnosis_category(item) == category
                    for ref in item.refs}
            by_entity = Counter(entity_kind for entity_kind, _ in refs)
            affected = ', '.join(f'{count} {entity_kind}(s)'
                                 for entity_kind, count in sorted(by_entity.items()))
            groups.append((category, kind, grouped[category, kind], affected))
    groups = tuple(groups)
    return f'{status} {counts}. {audit}', groups


def repair_disabled_reason(project) -> str | None:
    if getattr(project, 'read_only_reason', None):
        return str(project.read_only_reason)
    if getattr(project, 'geometry_editing_disabled_reason', None):
        return str(project.geometry_editing_disabled_reason)
    if getattr(project, 'mesh_only', False):
        return 'Mesh-only projects have no editable geometry for cleanup.'
    if len(project.geometry.features) or project.geometry.features.baseline is not None:
        return 'Feature-backed geometry requires edits to feature intent; direct cleanup is refused.'
    return None


def review_project(project, options: CleanerOptions | None = None) -> CleanerReport:
    """Read the live model synchronously; no history or project normalization."""
    if project.mesh_only:
        raise GeometryError('Mesh-only projects have no geometry to review.')
    return review_geometry(project.geometry, options)


def _referenced_entities(project, *, region_errors=None):
    """Collect application-owned stable anchors without serializing/mutating.

    Dataclass/container traversal covers the registered project attachments;
    region resolution additionally protects query/boolean-selected geometry.
    """
    found = set()
    seen = set()
    def visit(value):
        if isinstance(value, EntityRef):
            found.add(value)
            return
        if value is None or isinstance(value, (str, bytes, int, float, bool)) or callable(value):
            return
        if id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, Mapping):
            for key, item in value.items():
                visit(key)
                visit(item)
        elif is_dataclass(value):
            for item in fields(value):
                visit(getattr(value, item.name))
        elif isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                visit(item)
    for item in fields(project):
        if item.name not in ('geometry', 'regions'):
            visit(getattr(project, item.name))
    candidates = tuple(EntityRef(kind, identifier)
                       for kind, store in (('vertex', project.geometry.vertices),
                                           ('edge', project.geometry.edges),
                                           ('face', project.geometry.faces))
                       for identifier in store)
    for region in project.regions:
        visit(region)
        try:
            visit(project.regions.resolve(region.id, geometry=project.geometry, candidates=candidates))
        except Exception as error:
            if region_errors is not None:
                region_errors.append((region.id, str(error)))
            else:
                raise GeometryError(f'Cannot establish safe cleanup with unresolved region {region.id}: {error}') from error
    return found


def _preflight(project, plan, *, read_only=False):
    if read_only:
        raise PermissionError('The document is read-only.')
    reason = repair_disabled_reason(project)
    if reason:
        raise PermissionError(reason)
    validate_repair_plan(project.geometry, plan)
    removals = {change.ref for change in plan.changes if change.operation == 'remove_unused_vertex'}
    if removals:
        protected = removals & _referenced_entities(project)
        if protected:
            raise GeometryError('Cleanup would remove application attachments/region anchors: '
                                + ', '.join(str(item) for item in sorted(protected, key=lambda item: (item.kind, item.id))))


def preview_project_repair(project, report: CleanerReport, *, finding_ids=(),
                           snap_vertices=(), grid_size=None, max_displacement=None) -> RepairPlan:
    reason = repair_disabled_reason(project)
    if reason:
        raise PermissionError(reason)
    plan = propose_repair(project.geometry, report, finding_ids=finding_ids,
                          snap_vertices=snap_vertices, grid_size=grid_size,
                          max_displacement=max_displacement)
    _preflight(project, plan)
    return plan


class GeometryRepairCommand(GeometryCommand):
    """One cleanup action using the established exact snapshot command path."""
    label = 'clean geometry'

    def __init__(self, plan: RepairPlan):
        super().__init__()
        self.plan = plan

    def preflight(self, project):
        _preflight(project, self.plan)
        if self.plan.is_noop:
            raise GeometryError('Empty cleanup must bypass command history.')

    def operate(self, project):
        return apply_repair(project.geometry, self.plan)

    def redo(self, project):
        reason = repair_disabled_reason(project)
        if reason:
            raise PermissionError(reason)
        return super().redo(project)


def command_for_project_repair(project, plan: RepairPlan, *, read_only=False) -> GeometryRepairCommand | None:
    """Use app.run/session.execute with this command for document invalidation."""
    _preflight(project, plan, read_only=read_only)
    return None if plan.is_noop else GeometryRepairCommand(plan)


def apply_project_repair(stack, plan: RepairPlan):
    """Headless CommandStack convenience. Document clients use session.execute."""
    command = command_for_project_repair(stack.project, plan)
    return () if command is None else stack.run(command)



def diagnose_project(project, options: CleanerOptions | None = None, *, cancelled=None, progress=None):
    """Full read-only owner diagnosis plus project attachment resolution checks."""
    from dataclasses import replace
    from anygeometry.cleaner_diagnostics import diagnose_geometry, DiagnosisCheck, DiagnosisFinding
    from time import perf_counter
    started = perf_counter()
    report = diagnose_geometry(project.geometry, options, cancelled=cancelled, progress=progress)
    attachment_started = perf_counter()
    checks, findings = list(report.checks), list(report.findings)
    if project.mesh_only:
        checks.append(DiagnosisCheck('project_geometry', 'unsupported', 'Mesh-only project: geometry diagnosis does not cover the imported mesh.'))
        findings.append(DiagnosisFinding('project:mesh_only', 'mesh_only', (), 'Mesh-only project has no authored geometry to diagnose.', severity='blocker'))
    elif not any(check.status == 'cancelled' for check in checks):
        try:
            region_errors = []
            references = _referenced_entities(project, region_errors=region_errors)
            active_keys = project.geometry.entity_keys()
            missing = sorted((entity for entity in references if (entity.kind, entity.id) not in active_keys),
                             key=lambda entity: (entity.kind, entity.id))
            for entity in missing:
                findings.append(DiagnosisFinding(f'project:missing:{entity.kind}{entity.id}', 'missing_project_anchor', (entity,),
                    'A project attachment or region refers to missing geometry.', severity='error', action='Repair the attachment/region using explicit replacement lineage.'))
            for region_id, error in region_errors:
                findings.append(DiagnosisFinding(f'project:region:{region_id}', 'unresolved_project_region', (),
                    f'Region {region_id}: {error}', severity='error', action='Repair the region definition or missing anchors.'))
            checks.append(DiagnosisCheck('project_attachments', 'completed' if not region_errors else 'incomplete',
                'All stable project anchors checked; every unresolved region retained individually.', len(references)))
        except Exception as error:
            checks.append(DiagnosisCheck('project_attachments', 'failed', str(error)))
            findings.append(DiagnosisFinding('project:attachment_failure', 'project_attachment_failure', (), str(error), severity='blocker'))
    timings = tuple((name, value) for name, value in report.timings if name != 'total')
    timings += (('project_attachments', perf_counter() - attachment_started), ('total', perf_counter() - started))
    return replace(report, findings=tuple(sorted(findings, key=lambda item: item.id)), checks=tuple(checks), timings=timings)
