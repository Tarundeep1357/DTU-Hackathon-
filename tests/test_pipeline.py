import pytest

from evidence_arena.core.contracts import ClaimResponse, EvidenceResponse, Status
from evidence_arena.sample import sample_case
from evidence_arena.services.gemma_client import ModelError
from evidence_arena.services.pipeline import bounded_passages, run_analysis


def test_pipeline_full_grounded_case(environment):
    _, settings = environment
    sample, _ = sample_case()
    class Client:
        def generate(self, contract, *args, **kwargs):
            if contract == ClaimResponse:
                return ClaimResponse(claims=sample.claims)
            return EvidenceResponse(evidence=[e.edge for e in sample.edges], gaps=sample.gaps)
    events = []
    result = run_analysis(sample.question, sample.sources, sample.passages, settings, client=Client(), progress=lambda *x: events.append(x))
    assert result.mode == "live_gemma4"
    assert [v.status for v in result.verdicts] == [Status.CONTESTED, Status.UNVERIFIED]
    assert any(t.operation == "counterevidence_search" for t in result.trail)
    assert len(events) == 3


def test_pipeline_fails_closed_on_fabricated_reference(environment):
    _, settings = environment
    sample, _ = sample_case()
    edge = sample.edges[0].edge.model_copy(update={"passage_id": "invented"})
    class Client:
        def generate(self, contract, *args, **kwargs):
            return EvidenceResponse(evidence=[edge])
    result = run_analysis(sample.question, sample.sources, sample.passages, settings, claims=sample.claims, client=Client())
    assert result.verdicts[0].status == Status.UNVERIFIED
    assert not result.edges[0].citation_valid


def test_pipeline_extraction_warning_blocks_promotion(environment):
    _, settings = environment
    sample, _ = sample_case()
    sample.sources[2].warnings = ["Page 1: text not reliably extracted"]
    class Client:
        def generate(self, *args, **kwargs):
            return EvidenceResponse(evidence=[e.edge for e in sample.edges])
    result = run_analysis(sample.question, sample.sources, sample.passages, settings, claims=sample.claims, client=Client())
    assert all(v.status == Status.UNVERIFIED for v in result.verdicts)


def test_unknown_claim_rejected(environment):
    _, settings = environment
    sample, _ = sample_case()
    edge = sample.edges[0].edge.model_copy(update={"claim_id": "invented"})
    class Client:
        def generate(self, *args, **kwargs):
            return EvidenceResponse(evidence=[edge])
    with pytest.raises(ModelError, match="unknown claim"):
        run_analysis(sample.question, sample.sources, sample.passages, settings, claims=sample.claims, client=Client())


def test_context_budget_includes_multiple_sources():
    sample, _ = sample_case()
    selected = bounded_passages(sample.passages, 250)
    assert len({p.source_id for p in selected}) == 3

