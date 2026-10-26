"""Integrity, acquisition and freshness observations, never authenticity certification."""
from datetime import datetime, timezone
import hashlib
from urllib.parse import urlsplit

from pydantic import Field

from evidence_arena.core.contracts import Contract, Source, utcnow


class ProvenanceReport(Contract):
    source_id: str
    sha256_matches: bool
    acquisition_metadata_consistent: bool
    days_since_acquisition: int
    notes: list[str] = Field(default_factory=list)
    authenticity_certified: bool = False


def inspect_source(source: Source, content: bytes, now=None, stale_after_days=30):
    now = now or utcnow()
    acquired = source.acquired_at
    if acquired.tzinfo is None:
        acquired = acquired.replace(tzinfo=timezone.utc)
    age = (now - acquired).days
    notes = ["Hash agreement shows continuity with acquired bytes, not authenticity or accuracy.",
             "Publication date and independent source verification are not established by acquisition metadata."]
    consistent = age >= 0
    if source.acquisition_url:
        parsed = urlsplit(source.acquisition_url)
        consistent &= parsed.scheme == "https" and bool(parsed.hostname) and not bool(parsed.username or parsed.password)
    if age > stale_after_days:
        notes.append(f"Acquired more than {stale_after_days} days ago; check for a newer source version before relying on freshness.")
    if age < 0:
        notes.append("Acquisition timestamp is in the future; metadata requires review.")
    return ProvenanceReport(source_id=source.source_id, sha256_matches=hashlib.sha256(content).hexdigest() == source.sha256,
                             acquisition_metadata_consistent=bool(consistent), days_since_acquisition=max(age, 0), notes=notes)

