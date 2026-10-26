"""Fixed, visibly fictional walkthrough. This module never impersonates live inference."""
from datetime import date
from decimal import Decimal

from evidence_arena.config import Settings
from evidence_arena.core.compare import check_edge
from evidence_arena.core.contracts import Analysis, ChallengeEvent, Claim, EvidenceEdge, Facets
from evidence_arena.core.gap_engine import gap_for
from evidence_arena.core.ingest import ingest_many
from evidence_arena.core.verdict import adjudicate

SAMPLE_FILES = [
    ("A-monthly-report.txt", b"500 meals were served each day at Kitchen A from May 1-7, 2026."),
    ("B-daily-meal-log.txt", b"Kitchen A served 320 meals per day on May 1-7, 2026."),
    ("C-equipment-update.txt", b"New equipment was installed at Kitchen A on May 8, 2026. No later meal totals are recorded in this update."),
]


def sample_case(settings=None):
    settings = settings or Settings()
    items = ingest_many(SAMPLE_FILES, settings)
    passages = [p for i in items for p in i.passages]
    first = Facets(subject="Kitchen A", metric="meals served", value=500, unit="meals/day",
                   period_start=date(2026, 5, 1), period_end=date(2026, 5, 7), observed=True)
    second = first.model_copy(update={"period_start": date(2026, 5, 9), "period_end": date(2026, 5, 15)})
    claims = [Claim(claim_id="C1", text="Kitchen A served 500 meals/day on May 1–7, 2026", facets=first),
              Claim(claim_id="C2", text="Kitchen A served 500 meals/day on May 9–15, 2026", facets=second)]
    edges = [EvidenceEdge(claim_id="C1", passage_id=passages[0].passage_id, relation="supports", quote=passages[0].text, facets=first),
             EvidenceEdge(claim_id="C1", passage_id=passages[1].passage_id, relation="contradicts", quote=passages[1].text,
                          facets=first.model_copy(update={"value": Decimal(320)}))]
    lookup = {p.passage_id: p for p in passages}
    checked = [check_edge(claims[0], e, lookup) for e in edges]
    ids = [p.passage_id for p in passages]
    verdicts = [adjudicate(c, checked, ids) for c in claims]
    trail = [ChallengeEvent(operation="prepared_fixture_loaded", input_ids=[i.source.source_id for i in items],
                            result="Loaded three labelled fictional sources and fixed claim/edge proposals; no model inference occurred"),
             ChallengeEvent(operation="counterevidence_search", input_ids=ids, result="Inspected all three fixture passages, including the opposing daily log"),
             *[ChallengeEvent(operation="source_and_comparison_validation", input_ids=[e.edge.passage_id],
                              result="Executed exact quote validation and deterministic facet comparison") for e in checked],
             *[ChallengeEvent(operation="rule_adjudication", input_ids=[v.claim_id], result=v.status.value) for v in verdicts]]
    analysis = Analysis(question="Did Kitchen A serve 500 meals/day before and after the equipment installation?",
                        mode="prepared_walkthrough", sources=[i.source for i in items], passages=passages, claims=claims,
                        edges=checked, verdicts=verdicts, gaps=[gap_for(c) for c in claims], trail=trail,
                        warnings=["Fictional prepared walkthrough: fixed model proposals; validation and verdict rules execute locally."])
    return analysis, {item.source.source_id: data for item, (_, data) in zip(items, SAMPLE_FILES)}

