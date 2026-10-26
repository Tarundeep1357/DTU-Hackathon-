from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Status(str, Enum):
    SUPPORTED = "Supported"
    CONTRADICTED = "Contradicted"
    CONTESTED = "Contested"
    UNVERIFIED = "Unverified"


class Facets(Contract):
    subject: str | None = None
    metric: str | None = None
    value: Decimal | None = None
    unit: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    observed: bool | None = None
    conditions: str | None = None

    @model_validator(mode="after")
    def ordered_period(self):
        if bool(self.period_start) != bool(self.period_end):
            raise ValueError("Provide both period endpoints or neither")
        if self.period_start and self.period_end < self.period_start:
            raise ValueError("Period ends before it starts")
        return self


class Source(Contract):
    source_id: str
    filename: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    file_type: Literal["pdf", "txt"]
    page_count: int = Field(ge=1)
    warnings: list[str] = Field(default_factory=list)
    acquisition_label: str = "User upload"
    acquisition_url: str | None = None
    acquired_at: datetime = Field(default_factory=utcnow)
    duplicate_of: str | None = None


class Passage(Contract):
    passage_id: str
    source_id: str
    page: int = Field(ge=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    text: str = Field(min_length=1)
    extraction_method: Literal["text", "ocr"] = "text"


class Claim(Contract):
    claim_id: str = Field(pattern=r"^C[1-9][0-9]*$")
    text: str = Field(min_length=10, max_length=1500)
    facets: Facets = Field(default_factory=Facets)


class EvidenceEdge(Contract):
    claim_id: str
    passage_id: str
    relation: Literal["supports", "contradicts", "context", "irrelevant"]
    quote: str = Field(min_length=1, max_length=6000)
    facets: Facets = Field(default_factory=Facets)


class CheckedEdge(Contract):
    edge: EvidenceEdge
    citation_valid: bool
    comparable: bool
    reasons: list[str] = Field(default_factory=list)


class Verdict(Contract):
    claim_id: str
    status: Status
    inspected_passage_ids: list[str]
    supporting_ids: list[str] = Field(default_factory=list)
    opposing_ids: list[str] = Field(default_factory=list)
    reasoning: str
    limitations: list[str] = Field(default_factory=list)
    strength: str


class Outcome(Contract):
    observation: str = Field(min_length=5, max_length=1500)
    effect: str = Field(min_length=5, max_length=1500)


class GapTest(Contract):
    claim_id: str
    requested_record: str = Field(min_length=5, max_length=1500)
    why: str = Field(min_length=5, max_length=1500)
    expected_source: str = Field(min_length=3, max_length=500)
    hypotheses: list[str] = Field(min_length=2, max_length=4)
    outcome_a: Outcome
    outcome_b: Outcome


class ChallengeEvent(Contract):
    timestamp: datetime = Field(default_factory=utcnow)
    operation: str
    input_ids: list[str] = Field(default_factory=list)
    result: str


class Analysis(Contract):
    question: str
    mode: Literal["live_gemma4", "prepared_walkthrough"]
    sources: list[Source]
    passages: list[Passage]
    claims: list[Claim]
    edges: list[CheckedEdge] = Field(default_factory=list)
    verdicts: list[Verdict] = Field(default_factory=list)
    gaps: list[GapTest] = Field(default_factory=list)
    trail: list[ChallengeEvent] = Field(default_factory=list)
    model_id: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ClaimResponse(Contract):
    claims: list[Claim] = Field(min_length=1, max_length=20)


class EvidenceResponse(Contract):
    evidence: list[EvidenceEdge] = Field(max_length=240)
    gaps: list[GapTest] = Field(default_factory=list, max_length=20)


class EvidenceProposal(Contract):
    claim_id: str
    passage_id: str
    relation: Literal["supports", "contradicts", "context"]
    quote: str = Field(min_length=1,max_length=3000)


class ProposalResponse(Contract):
    evidence: list[EvidenceProposal] = Field(max_length=60)


class ReviewAnnotation(Contract):
    annotation_id: str = Field(default_factory=lambda: uid("R"))
    claim_id: str | None = None
    source_id: str | None = None
    kind: Literal["comment", "decision", "override", "source_note"]
    text: str = Field(min_length=1, max_length=5000)
    override_status: Status | None = None
    author_id: str
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def override_needs_claim(self):
        if self.kind == "override" and (not self.claim_id or not self.override_status):
            raise ValueError("An override needs a claim and status; text provides its reason")
        return self


class Membership(Contract):
    case_id: str
    user_id: str
    role: Literal["owner", "reviewer", "viewer"]


class CaseSnapshot(Contract):
    case_id: str
    title: str
    version: int
    analysis: Analysis
    annotations: list[ReviewAnnotation] = Field(default_factory=list)

