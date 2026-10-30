"""Barge diagnosis remains a read-only, lossless audit with scoped corrections."""
import json
from collections import Counter

from anyfem.application.examples import floating_barge_large
from anyfem.geometry_cleaner import diagnose_project, diagnosis_summary
from anyfem.io.project_file import project_to_dict


def test_default_barge_diagnosis_preserves_errors_coverage_and_model(tmp_path):
    project = floating_barge_large()
    before = project_to_dict(project)
    report = diagnose_project(project)
    assert project_to_dict(project) == before
    raw = report.to_dict()
    (tmp_path / 'after-barge.json').write_text(json.dumps(raw, indent=2), encoding='utf-8')
    assert not report.complete and not report.clean and not report.audit_clean
    assert report.counts['by_severity'] == {'blocker': 247, 'error': 912, 'info': 424, 'warning': 3}
    assert len(report.findings) == 1586
    attachments = [item for item in report.findings if item.kind == 'attachment_inconsistent']
    assert len(attachments) == 40
    attachment_ids = {ref.id for item in attachments for ref in item.refs if ref.kind == 'attachment'}
    assert len(attachment_ids) == 4
    assert {project.geometry.attachments[key].kind.value for key in attachment_ids} == {'member_on_face'}
    # The new boundary kind never makes unsupported curved predicates pass.
    assert report.counts['by_kind']['unclassified_candidate'] == 220
    assert report.counts['by_kind']['unverified_classification'] == 1
    assert report.counts['incomplete_checks'] == 3
    assert raw['owner_audit'] == report.audit.to_dict()
    assert Counter(item['severity'] for item in raw['findings']) == report.counts['by_severity']
    summary, groups = diagnosis_summary(report)
    assert '912 model errors' in summary
    assert '247 coverage limitations' in summary
    assert 'Diagnosis incomplete' in summary
    group = next(item for item in groups if item[1] == 'attachment_inconsistent')
    assert group[0] == 'Model errors' and group[2] == 40
    assert '4 attachment(s)' in group[3] and '4 member(s)' in group[3]
    assert sum(item[2] for item in groups) == len(report.findings)
