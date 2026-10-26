"""Small-corpus TF-IDF plus a separate counterevidence search."""
from collections import Counter
import math
import re

from .contracts import Claim, Passage

STOP = {"the", "a", "an", "of", "in", "on", "at", "to", "and", "was", "were", "is", "did", "it", "for"}


def tokens(text):
    return [x for x in re.findall(r"[a-z0-9]+", text.lower()) if x not in STOP]


def retrieve(claim: Claim, passages: list[Passage], limit=6) -> tuple[list[Passage], list[str]]:
    if not passages:
        return [], []
    counts = [Counter(tokens(p.text)) for p in passages]
    df = Counter(term for row in counts for term in row)
    query = Counter(tokens(claim.text))
    def score(row, terms):
        return sum(min(row[t], 3) * weight * (math.log((1 + len(passages)) / (1 + df[t])) + 1)
                   for t, weight in terms.items()) / math.sqrt(max(sum(row.values()), 1))
    ranked = sorted(range(len(passages)), key=lambda i: (-score(counts[i], query), passages[i].passage_id))
    # Remove the expected quantity from the counter-query; preserve subject and metric.
    counter_query = Counter({t: n for t, n in query.items() if not t.isdigit()})
    counter_ranked = sorted(range(len(passages)), key=lambda i: (
        -(score(counts[i], counter_query) + (0.4 if re.search(r"\b(not|never|only|instead|however)\b", passages[i].text, re.I) else 0)),
        passages[i].passage_id))
    chosen = []
    for i in ranked[:max(1, limit // 2)] + counter_ranked + ranked:
        if i not in chosen and score(counts[i], counter_query or query) > 0:
            chosen.append(i)
        if len(chosen) >= limit:
            break
    counter_ids = [passages[i].passage_id for i in counter_ranked[:limit] if i in chosen]
    return [passages[i] for i in chosen], counter_ids

