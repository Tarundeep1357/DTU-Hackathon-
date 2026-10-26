from .contracts import Claim, GapTest, Outcome


def gap_for(claim: Claim, domain="programme-audit") -> GapTest:
    f = claim.facets
    period = f"{f.period_start} through {f.period_end}" if f.period_start else "the claim's period, to be specified before comparison"
    scope = f.subject or "the named population/site"
    metric = f.metric or "the claimed observation"
    unit = f.unit or "the same measurement definition"
    record = {"programme-audit": "Dated, signed primary activity ledger", "research-review": "Original study results and methods record",
              "compliance-review": "Dated control execution record and governing requirement"}.get(domain, "Dated primary observation record")
    return GapTest(claim_id=claim.claim_id,
                   requested_record=f"{record} for {scope}, {period}, measuring {metric} in {unit}.",
                   why="A primary observation using the same scope and period distinguishes the claim from an alternative account. Existing disputes still require reconciliation.",
                   expected_source="Record custodian; verify dates, measurement definitions, completeness and acquisition provenance",
                   hypotheses=["The precise claim is supported by the primary records", "The primary records establish a different result or remain incomplete"],
                   outcome_a=Outcome(observation=f"Complete, provenance-checked records match: {claim.text}",
                                     effect="Adds support for that scope and period. If comparable opposition remains, the finding stays Contested until reconciled."),
                   outcome_b=Outcome(observation="Complete comparable records establish a different value or outcome.",
                                     effect="Adds opposing evidence. Contradicted if no material support remains; otherwise Contested. Incomplete records leave it Unverified."))

