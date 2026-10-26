import json

import pytest

from evidence_arena.core.compare import check_edge, normalize_quote
from evidence_arena.core.contracts import Status
from evidence_arena.core.retrieval import retrieve
from evidence_arena.evaluation import benchmark_cases, evaluate, run_case
from evidence_arena.sample import sample_case
from evidence_arena.services.exports import evidence_packet, export_payload, json_export, verified_analysis


@pytest.mark.parametrize("case", benchmark_cases(), ids=lambda c: c["id"])
def test_labelled_statuses(case):
    verdict, _ = run_case(case)
    assert verdict.status.value == case["expected"]


def test_meal_case():
    a, _ = sample_case()
    assert [v.status for v in a.verdicts] == [Status.CONTESTED, Status.UNVERIFIED]
    assert "2026-05-01" in a.gaps[0].requested_record
    assert "2026-05-09" in a.gaps[1].requested_record
    assert all(e.citation_valid for e in a.edges)


def test_numeric_relation_must_match():
    a, _ = sample_case()
    edge = a.edges[1].edge.model_copy(update={"relation": "supports"})
    checked = check_edge(a.claims[0], edge, {p.passage_id: p for p in a.passages})
    assert checked.citation_valid and not checked.comparable


def test_model_cannot_fabricate_scope():
    a, _ = sample_case()
    edge = a.edges[0].edge.model_copy(deep=True)
    edge.facets.subject = "Kitchen B"
    assert not check_edge(a.claims[0], edge, {p.passage_id: p for p in a.passages}).comparable


def test_claim_facets_cannot_disagree_with_displayed_claim():
    from decimal import Decimal
    a, _ = sample_case()
    claim = a.claims[0].model_copy(deep=True)
    claim.facets.value = Decimal(320)
    checked = check_edge(claim, a.edges[1].edge, {p.passage_id: p for p in a.passages})
    assert not checked.comparable
    assert any("Claim quantity" in reason for reason in checked.reasons)


def test_whitespace_only_normalization():
    assert normalize_quote("a\n b\t500") == "a b 500"
    a, _ = sample_case()
    edge = a.edges[0].edge.model_copy(update={"quote": a.edges[0].edge.quote.replace("500", "501")})
    assert not check_edge(a.claims[0], edge, {p.passage_id: p for p in a.passages}).citation_valid


def test_retrieval_finds_counterevidence():
    a, _ = sample_case()
    selected, counter = retrieve(a.claims[0], a.passages)
    assert a.passages[0] in selected and a.passages[1] in selected
    assert a.passages[1].passage_id in counter


def test_export_revalidates_and_suppresses_fabrication():
    a, _ = sample_case()
    a.edges[0].edge.quote = "FABRICATED QUOTATION THAT MUST NOT BE EXPORTED"
    exported = json_export(a).decode()
    assert "FABRICATED QUOTATION" not in exported
    assert json.loads(exported)["analysis"]["verdicts"][0]["status"] == "Unverified"


def test_packet_checks_integrity_and_paths():
    import io
    import zipfile
    a, files = sample_case()
    with zipfile.ZipFile(io.BytesIO(evidence_packet(a, files))) as archive:
        assert "manifest.json" in archive.namelist()
        assert all(".." not in name for name in archive.namelist())
    files[a.sources[0].source_id] = b"altered"
    with pytest.raises(ValueError, match="integrity"):
        evidence_packet(a, files)


def test_invalid_provenance_is_rejected():
    a, _ = sample_case()
    a.passages[0].source_id = "unknown"
    with pytest.raises(ValueError, match="provenance"):
        verified_analysis(a)


def test_evaluation_gate():
    result = evaluate()
    assert result["failed"] == 0 and result["false_conflict_count"] == 0

