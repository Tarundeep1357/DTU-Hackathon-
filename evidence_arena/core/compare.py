"""Conservative, inspectable comparisons; the model's scope flag is not trusted."""
import calendar
from datetime import date
from decimal import Decimal
import re

from .contracts import CheckedEdge, Claim, EvidenceEdge, Facets, Passage


def normalize_quote(text: str) -> str:
    """Only whitespace is normalized. Punctuation, case and digits are retained."""
    return " ".join(text.split())


def canonical(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() if text else ""


def periods(text: str) -> set[tuple[date, date]]:
    result = set()
    for match in re.finditer(r"(20\d{2}-\d{2}-\d{2})(?:\s*(?:to|through|[–—])\s*(20\d{2}-\d{2}-\d{2}))?", text):
        try:
            start = date.fromisoformat(match[1])
            result.add((start, date.fromisoformat(match[2]) if match[2] else start))
        except ValueError:
            pass
    months = "|".join(calendar.month_name[1:])
    for match in re.finditer(rf"\b({months})\s+(\d{{1,2}})(?:\s*[-–—]\s*(\d{{1,2}}))?,?\s+(20\d{{2}})\b", text, re.I):
        try:
            month = [m.lower() for m in calendar.month_name].index(match[1].lower())
            result.add((date(int(match[4]), month, int(match[2])), date(int(match[4]), month, int(match[3] or match[2]))))
        except ValueError:
            pass
    return result


def unit_key(unit: str | None) -> str:
    value = canonical(unit)
    for old, new in [("per day", "day"), ("daily", "day"), ("per week", "week"), ("weekly", "week"),
                     ("per month", "month"), ("monthly", "month")]:
        value = value.replace(old, new)
    return value


def words_grounded(label: str, text: str) -> bool:
    # Token order may change ("meals served" vs "served meals"), but not meaning-bearing words.
    label_words = set(canonical(label).split()) - {"per", "the", "of"}
    text_words = set(canonical(text).split())
    return bool(label_words) and label_words <= text_words


def facets_grounded(facets: Facets, quote: str) -> list[str]:
    errors = []
    for field in ("subject", "metric", "conditions"):
        value = getattr(facets, field)
        if value and not words_grounded(value, quote):
            errors.append(f"Evidence {field} is not grounded in the quotation")
    if facets.value is not None:
        values = {Decimal(m.replace(",", "")) for m in re.findall(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?![\w.])", quote)}
        if facets.value not in values:
            errors.append("Evidence quantity is not present in the quotation")
    if facets.period_start and (facets.period_start, facets.period_end) not in periods(quote):
        errors.append("Evidence period is not explicit in the quotation")
    if facets.unit:
        key = unit_key(facets.unit)
        normalized = unit_key(quote)
        # each day and per day are equivalent for these explicit rates.
        normalized = normalized.replace("each day", "day").replace("a day", "day")
        if not set(key.split()) <= set(normalized.split()):
            errors.append("Evidence unit is not explicit in the quotation")
    if facets.observed is True and re.search(r"\b(projected|projection|capacity|expected|target|forecast|planned|could)\b", quote, re.I):
        errors.append("A projection or capacity statement cannot establish observed activity")
    return errors


def compare_facets(claim: Facets, evidence: Facets) -> list[str]:
    reasons = []
    for field in ("subject", "metric", "conditions"):
        wanted, actual = getattr(claim, field), getattr(evidence, field)
        if wanted and canonical(wanted) != canonical(actual):
            reasons.append(f"{field.capitalize()} mismatch or missing context")
    if claim.unit and unit_key(claim.unit) != unit_key(evidence.unit):
        reasons.append("Unit mismatch; no unsupported rate conversion")
    if claim.period_start and (claim.period_start, claim.period_end) != (evidence.period_start, evidence.period_end):
        reasons.append("Period mismatch or unspecified observation window")
    if claim.observed is not None and claim.observed != evidence.observed:
        reasons.append("Observed activity and projection are not interchangeable")
    if claim.value is not None and evidence.value is None:
        reasons.append("Missing evidence quantity")
    return reasons


def check_edge(claim: Claim, edge: EvidenceEdge, passages: dict[str, Passage]) -> CheckedEdge:
    passage = passages.get(edge.passage_id)
    valid = (passage is not None and edge.claim_id == claim.claim_id and
             normalize_quote(edge.quote) in normalize_quote(passage.text))
    if not valid:
        return CheckedEdge(edge=edge, citation_valid=False, comparable=False,
                           reasons=["Unknown source reference, claim reference, or altered quotation"])
    reasons = [r.replace("Evidence", "Claim") for r in facets_grounded(claim.facets, claim.text)]
    reasons += facets_grounded(edge.facets, edge.quote) + compare_facets(claim.facets, edge.facets)
    if not claim.facets.subject or not claim.facets.metric:
        reasons.append("Claim needs an identifiable subject and metric/predicate")
    if claim.facets.value is not None and not claim.facets.unit:
        reasons.append("Quantitative claim needs an explicit measurement unit")
    if periods(claim.text) and not claim.facets.period_start:
        reasons.append("Claim contains a period that was omitted from its comparison fields")
    if not reasons and claim.facets.value is not None and edge.relation in {"supports", "contradicts"}:
        same = claim.facets.value == edge.facets.value
        if same != (edge.relation == "supports"):
            reasons.append("Model relation disagrees with the quoted numeric values")
    return CheckedEdge(edge=edge, citation_valid=True, comparable=not reasons, reasons=reasons)

