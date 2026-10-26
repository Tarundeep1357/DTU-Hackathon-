from .contracts import CheckedEdge, Claim, Status, Verdict


def adjudicate(claim: Claim, edges: list[CheckedEdge], inspected: list[str], *, extraction_incomplete=False) -> Verdict:
    own = [e for e in edges if e.edge.claim_id == claim.claim_id]
    supporting = sorted({e.edge.passage_id for e in own if e.citation_valid and e.comparable and e.edge.relation == "supports"})
    opposing = sorted({e.edge.passage_id for e in own if e.citation_valid and e.comparable and e.edge.relation == "contradicts"})
    limitations = sorted({reason for e in own for reason in e.reasons})
    if any(not e.citation_valid for e in own):
        status = Status.UNVERIFIED
        reason = "Source validation failed. This claim needs a fresh, valid evidence review."
    elif extraction_incomplete:
        status = Status.UNVERIFIED
        reason = "Incomplete source extraction prevents a reliable review of the supplied set."
        limitations.append("One or more supplied pages could not be reliably extracted")
    elif supporting and opposing:
        status = Status.CONTESTED
        reason = "Comparable supplied passages both support and oppose this claim."
    elif supporting:
        status = Status.SUPPORTED
        reason = "Validated comparable support was found; no material opposition was detected in the reviewed set."
    elif opposing:
        status = Status.CONTRADICTED
        reason = "Validated comparable opposition was found without matching support in the reviewed set."
    else:
        status = Status.UNVERIFIED
        reason = "No sufficient comparable evidence establishes or contradicts this precise claim."
    limitations.append("Source-scoped assessment; no authentication of document contents or external verification")
    return Verdict(claim_id=claim.claim_id, status=status, inspected_passage_ids=sorted(set(inspected)),
                   supporting_ids=supporting, opposing_ids=opposing, reasoning=reason,
                   limitations=limitations, strength=f"{len(supporting)} supporting / {len(opposing)} opposing passages; independence is not certified")

