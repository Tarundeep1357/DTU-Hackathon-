from collections.abc import Callable

from evidence_arena.config import Settings
from evidence_arena.core.compare import check_edge
from evidence_arena.core.contracts import Analysis, ChallengeEvent, ClaimResponse, EvidenceResponse, Status
from evidence_arena.core.gap_engine import gap_for
from evidence_arena.core.retrieval import retrieve
from evidence_arena.core.templates import TEMPLATES
from evidence_arena.core.verdict import adjudicate
from .gemma_client import GemmaClient, ModelError


def bounded_passages(passages, character_budget=18000):
    selected, size = [], 0
    # Round-robin sources so the budget cannot silently exclude all counterevidence
    # simply because an earlier file was long.
    groups = {}
    for passage in passages:
        groups.setdefault(passage.source_id, []).append(passage)
    while groups:
        for source in list(groups):
            passage = groups[source].pop(0)
            if size + len(passage.text) <= character_budget:
                selected.append(passage)
                size += len(passage.text)
            if not groups[source]:
                del groups[source]
    return selected


def run_analysis(question, sources, passages, settings: Settings, *, claims=None,
                 domain="programme-audit", progress: Callable = lambda *_: None, client=None):
    client = client or GemmaClient(settings)
    trail = [ChallengeEvent(operation="ingestion_review", input_ids=[s.source_id for s in sources],
                            result=f"Received {len(sources)} source records and {len(passages)} extracted passages")]
    if not question.strip() or len(question) > 4000:
        raise ValueError("Enter a question of 1–4000 characters")
    progress(10, "Proposing atomic claims")
    if claims is None:
        response = client.generate(ClaimResponse, f"Propose at most {settings.max_claims} precise atomic claims to test, not conclusions. Use C1, C2, etc. Split pre/post periods. Do not fill unspecified dates with guesses.",
                                   {"question": question, "domain": TEMPLATES.get(domain, {}),
                                    "passages": [p.model_dump(mode="json") for p in bounded_passages(passages)]})
        claims = response.claims
    if not 1 <= len(claims) <= settings.max_claims or len({c.claim_id for c in claims}) != len(claims):
        raise ModelError("Claim count or identifiers are invalid")
    trail.append(ChallengeEvent(operation="claim_review", input_ids=[c.claim_id for c in claims], result=f"Validated {len(claims)} claims"))
    bundles, inspected = [], {}
    for claim in claims:
        candidates, counter_ids = retrieve(claim, passages, settings.retrieval_limit)
        inspected[claim.claim_id] = [p.passage_id for p in candidates]
        bundles.append({"claim": claim.model_dump(mode="json"), "passages": [p.model_dump(mode="json") for p in candidates]})
        trail.extend([
            ChallengeEvent(operation="candidate_retrieval", input_ids=[claim.claim_id], result=f"Selected {len(candidates)} of {len(passages)} passages"),
            ChallengeEvent(operation="counterevidence_search", input_ids=counter_ids, result="Ran a separate search without the expected quantity and with negation candidates")])
    progress(35, "Gemma 4 is reviewing candidate evidence")
    if sum(len(p["text"]) for bundle in bundles for p in bundle["passages"]) > 24000:
        raise ModelError("Candidate evidence exceeds the local model context budget; narrow the question or documents")
    response = client.generate(EvidenceResponse, "Classify supplied candidate passages for each claim. Return at most 3 material evidence edges per claim; include both supporting and opposing evidence when present, and at most one context edge. Skip irrelevant passages. Copy a concise exact quotation that contains all comparison facts. Provide grounded facets. Set gaps to an empty array: the application will generate discriminating tests from the validated claims. Do not issue verdicts.",
                               {"question": question, "bundles": bundles, "gap_guidance": TEMPLATES.get(domain, {}).get("gap_prompt", "")}, max_tokens=6500)
    lookup, claim_lookup = {p.passage_id: p for p in passages}, {c.claim_id: c for c in claims}
    checked = []
    for edge in response.evidence:
        if edge.claim_id not in claim_lookup:
            raise ModelError("Model referenced an unknown claim; no analysis was accepted")
        check = check_edge(claim_lookup[edge.claim_id], edge, lookup)
        if edge.passage_id not in inspected[edge.claim_id]:
            check.citation_valid = False
            check.comparable = False
            check.reasons.append("Passage was not supplied for this claim's review")
        checked.append(check)
        trail.append(ChallengeEvent(operation="source_and_comparison_validation", input_ids=[edge.claim_id, edge.passage_id],
                                    result="Citation and comparison passed" if check.citation_valid and check.comparable else "; ".join(check.reasons)))
    progress(80, "Applying evidence rules and generating the challenge trail")
    incomplete = any(any("not reliably extracted" in w or "replacement characters" in w for w in s.warnings) for s in sources)
    verdicts = [adjudicate(c, checked, inspected[c.claim_id], extraction_incomplete=incomplete) for c in claims]
    gaps_by_id = {g.claim_id: g for g in response.gaps if g.claim_id in claim_lookup}
    gaps = [gaps_by_id.get(v.claim_id) or gap_for(claim_lookup[v.claim_id], domain) for v in verdicts if v.status in {Status.CONTESTED, Status.UNVERIFIED}]
    for v in verdicts:
        trail.append(ChallengeEvent(operation="rule_adjudication", input_ids=[v.claim_id], result=f"{v.status.value}: {v.reasoning}"))
    return Analysis(question=question, mode="live_gemma4", sources=sources, passages=passages, claims=claims,
                    edges=checked, verdicts=verdicts, gaps=gaps, trail=trail, model_id=settings.gemma_model_id,
                    warnings=[w for s in sources for w in s.warnings])

