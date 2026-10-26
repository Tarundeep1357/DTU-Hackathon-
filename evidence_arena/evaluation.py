"""Labelled deterministic benchmark, explicitly distinct from live model quality."""
from datetime import date

from .config import Settings
from .core.compare import check_edge
from .core.contracts import Claim, EvidenceEdge, Facets, Status, utcnow
from .core.ingest import ingest
from .core.verdict import adjudicate
from .sample import sample_case


def benchmark_cases():
    return [
        {"id": "support", "value": 500, "relation": "supports", "expected": "Supported"},
        {"id": "opposition", "value": 320, "relation": "contradicts", "expected": "Contradicted"},
        {"id": "different_period", "value": 320, "relation": "contradicts", "period": (9, 15), "expected": "Unverified"},
        {"id": "different_unit", "value": 320, "relation": "contradicts", "unit": "meals/week", "expected": "Unverified"},
        {"id": "different_site", "value": 320, "relation": "contradicts", "site": "Kitchen B", "expected": "Unverified"},
        {"id": "different_metric", "value": 320, "relation": "contradicts", "metric": "meals prepared", "expected": "Unverified"},
        {"id": "projection", "value": 320, "relation": "contradicts", "observed": False, "expected": "Unverified"},
        {"id": "altered_quote", "value": 500, "relation": "supports", "bad_quote": True, "expected": "Unverified"},
        {"id": "unknown_passage", "value": 500, "relation": "supports", "bad_id": True, "expected": "Unverified"},
        {"id": "no_evidence", "expected": "Unverified"},
    ]


def run_case(case):
    facets = Facets(subject="Kitchen A", metric="meals served", value=500, unit="meals/day",
                    period_start=date(2026, 5, 1), period_end=date(2026, 5, 7), observed=True)
    claim = Claim(claim_id="C1", text="Kitchen A served 500 meals/day May 1–7, 2026", facets=facets)
    if "value" not in case:
        return adjudicate(claim, [], []), []
    start, end = case.get("period", (1, 7))
    unit = case.get("unit", "meals/day")
    metric = case.get("metric", "meals served")
    site = case.get("site", "Kitchen A")
    observed = case.get("observed", True)
    text = f"{site}: {case['value']} {metric} per {'week' if unit.endswith('week') else 'day'} from May {start}-{end}, 2026."
    if not observed:
        text = "Projected capacity: " + text
    item = ingest("benchmark.txt", text.encode(), Settings())
    passage = item.passages[0]
    edge = EvidenceEdge(claim_id="C1", passage_id="fabricated" if case.get("bad_id") else passage.passage_id,
                         relation=case["relation"], quote="Fabricated quotation" if case.get("bad_quote") else passage.text,
                         facets=Facets(subject=site, metric=metric, value=case["value"], unit=unit,
                                       period_start=date(2026, 5, start), period_end=date(2026, 5, end), observed=observed))
    checked = check_edge(claim, edge, {passage.passage_id: passage})
    return adjudicate(claim, [checked], [passage.passage_id]), [checked]


def evaluate():
    rows = []
    invalid_displayed, false_conflicts = 0, 0
    for case in benchmark_cases():
        verdict, edges = run_case(case)
        rows.append({"id": case["id"], "expected": case["expected"], "actual": verdict.status.value,
                     "passed": verdict.status.value == case["expected"]})
        if case["id"].startswith("different_") and verdict.status in {Status.CONTRADICTED, Status.CONTESTED}:
            false_conflicts += 1
        invalid_displayed += sum(not e.citation_valid and e.comparable for e in edges)
    sample, _ = sample_case()
    for verdict, expected in zip(sample.verdicts, [Status.CONTESTED, Status.UNVERIFIED]):
        rows.append({"id": f"meal_{verdict.claim_id}", "expected": expected.value, "actual": verdict.status.value,
                     "passed": verdict.status == expected})
    return {"timestamp": utcnow().isoformat(), "benchmark": "Deterministic rules and prepared proposals; not live model accuracy",
            "total": len(rows), "passed": sum(r["passed"] for r in rows), "failed": sum(not r["passed"] for r in rows),
            "false_conflict_count": false_conflicts, "invalid_citations_promoted": invalid_displayed,
            "cases": rows}

