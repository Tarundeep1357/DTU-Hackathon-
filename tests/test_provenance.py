from datetime import timedelta

from evidence_arena.core.contracts import utcnow
from evidence_arena.sample import sample_case
from evidence_arena.services.provenance import inspect_source


def test_provenance_integrity_and_freshness():
    a, files = sample_case()
    source = a.sources[0]
    report = inspect_source(source, files[source.source_id], now=source.acquired_at + timedelta(days=40))
    assert report.sha256_matches and not report.authenticity_certified
    assert report.days_since_acquisition == 40
    assert any("newer source" in note for note in report.notes)
    assert not inspect_source(source, b"corrupt").sha256_matches


def test_future_acquisition_requires_review():
    a, files = sample_case()
    source = a.sources[0]
    source.acquired_at = utcnow() + timedelta(days=2)
    assert not inspect_source(source, files[source.source_id]).acquisition_metadata_consistent

