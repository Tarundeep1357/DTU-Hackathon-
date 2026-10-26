"""Conservative extraction from quoted text, independent of model scope labels."""
import calendar
from decimal import Decimal
import re

from .compare import canonical, periods, words_grounded
from .contracts import Facets


def derive_facets(claim: Facets, quote: str) -> Facets:
    subject = claim.subject if claim.subject and re.search(r"\b"+re.escape(canonical(claim.subject))+r"\b",canonical(quote)) else None
    metric = claim.metric if claim.metric and words_grounded(claim.metric,quote) else None
    windows=periods(quote)
    start,end=next(iter(windows)) if len(windows)==1 else (None,None)
    # Dates cannot be mistaken for measured quantities.
    months="|".join(calendar.month_name[1:])
    without_dates=re.sub(rf"\b({months})\s+\d{{1,2}}(?:\s*[-–—]\s*\d{{1,2}})?,?\s+20\d{{2}}\b"," ",quote,flags=re.I)
    without_dates=re.sub(r"20\d{2}-\d{2}-\d{2}"," ",without_dates)
    values={Decimal(x.replace(",","")) for x in re.findall(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?![\w.])",without_dates)}
    value=next(iter(values)) if len(values)==1 and claim.value is not None else None
    unit=None
    if claim.unit:
        base=re.split(r"/|\bper\b",claim.unit)[0].strip()
        if words_grounded(base,quote):
            period_match=re.search(r"\b(?:per|each|a)\s+(day|week|month|year)\b",quote,re.I)
            if period_match:
                unit=base+"/"+period_match[1].lower()
            elif re.search(r"\b(daily|weekly|monthly|yearly)\b",quote,re.I):
                word=re.search(r"\b(daily|weekly|monthly|yearly)\b",quote,re.I)[1].lower()
                unit=base+"/"+{"daily":"day","weekly":"week","monthly":"month","yearly":"year"}[word]
            elif "/" not in claim.unit and "per" not in claim.unit:
                unit=base
    projection=bool(re.search(r"\b(projected|projection|capacity|expected|target|forecast|planned|could|will)\b",quote,re.I))
    observed=False if projection else True if claim.observed is True and metric else None
    return Facets(subject=subject,metric=metric,value=value,unit=unit,period_start=start,period_end=end,
                   observed=observed,conditions=claim.conditions if claim.conditions and words_grounded(claim.conditions,quote) else None)

