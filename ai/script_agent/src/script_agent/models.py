from .use_assessment import UseAssessment
from functools import lru_cache
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator, model_serializer
from .fact_source import fact_metadata

ProductionType = Literal["창작·각색형", "사실해설형", "사실서사형", "실행안내형", "해석·감상형", "원문낭독형"]
CategoryCode = Literal[
    'C01', 'C02', 'C03', 'C04', 'C05', 'C06', 'C07',
    'C08', 'C09', 'C10', 'C11', 'C12', 'C13', 'C14',
    'C15', 'C16', 'C17', 'C18', 'C19', 'C20', 'C21',
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class License(StrictModel):
    kind: str = "unknown"
    version: str | None = None
    commercial: bool | None = None
    modify: bool | None = None
    share_alike: bool | None = None
    url: str | None = None
    evidence: str | None = None

    @field_validator('kind', mode='before')
    @classmethod
    def unknown_kind(cls, value):
        if value is None:
            return 'unknown'
        return (value.strip() or 'unknown') if isinstance(value, str) else value


class OriginalSource(StrictModel):
    """Provenance of a supplied summary, not a fetched or verified snapshot."""
    title: str | None = None
    source_url: str | None = None
    author: str | None = None
    license: License = Field(default_factory=License)
    attribution: str | None = None


class SourceMeta(StrictModel):
    source_id: str | None = None
    source_url: str | None = None
    author: str | None = None
    license: License = Field(default_factory=License)
    attribution: str | None = None
    info_as_of: str | None = None
    collected_at: str | None = None
    edition: str | None = None
    category: CategoryCode | None = None
    production_type: ProductionType | None = None
    target_sec: int = Field(default=90, ge=15, le=600)
    reading_text: str | None = None
    notes: str = ""
    fixture: bool = False
    source_kind: Literal['original', 'user_summary'] = 'original'
    original_sources: list[OriginalSource] = Field(default_factory=list)

    @model_serializer(mode='wrap')
    def serialize_fact_metadata(self, handler):
        return fact_metadata(handler(self))

    @field_validator('info_as_of', 'collected_at', mode='before')
    @classmethod
    def date_to_string(cls, value):
        from datetime import date
        return value.isoformat() if isinstance(value, date) else value

    @field_validator("source_id")
    @classmethod
    def safe_id(cls, value):
        import re
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
            raise ValueError("source_id must be a safe identifier")
        return value


class Fact(StrictModel):
    id: str = Field(pattern=r"^f[1-9][0-9]*$", description="Material card ID, including opinion cards: f1, f2, ...; collector F/O IDs belong in conditions.")
    text: str
    quote: str
    kind: Literal["source_statement", "opinion", "fiction"]
    conditions: list[str]


class Material(StrictModel):
    production_type: ProductionType
    category: CategoryCode
    routing_reason: str
    summary: str
    conflict: str
    details: list[str]
    question: str
    facts: list[Fact]
    unknowns: list[str]
    outline: list[str]


class Claim(StrictModel):
    sentence: int = Field(ge=1)
    kind: Literal["fact", "source_statement", "opinion", "fiction", "transition", "quotation"]
    fact_ids: list[str]


class AdaptationNote(StrictModel):
    from_source: list[str]
    new: list[str]


class Draft(StrictModel):
    title: str
    format: Literal["이야기형", "정보형", "본문낭독형", "감상형", "안내형"]
    tags: list[str]
    script: list[str] = Field(min_length=1)
    claims: list[Claim]
    adaptation_note: AdaptationNote
    unresolved: list[str]
    changes: list[str]


class Issue(StrictModel):
    sentence: int = Field(ge=0)
    problem: str
    reason: str
    direction: str


class RevisionResult(StrictModel):
    draft: Draft
    material: Material | None


def _category_enum(schema):
    # Represent a single Literal with the same enum keyword as CategoryCode.
    schema['enum'] = [schema.pop('const')]


@lru_cache(maxsize=42)
def category_response_schema(schema: type[BaseModel], category: str | None):
    """Constrain both provider JSON Schema and response validation to this route."""
    if category is None or schema not in (Material, RevisionResult):
        return schema
    if category not in get_args(CategoryCode):
        raise ValueError('invalid_category')
    material = create_model(
        'Material', __base__=Material,
        category=(Literal[category], Field(json_schema_extra=_category_enum)),
    )
    if schema is Material:
        return material
    return create_model('RevisionResult', __base__=RevisionResult,
                        material=(material | None, ...))


class Scores(StrictModel):
    grounding: int = Field(ge=1, le=5)
    listening: int = Field(ge=1, le=5)
    engagement: int = Field(ge=1, le=5)
    type_fit: int = Field(ge=1, le=5)


class EditorialCheck(StrictModel):
    requirement: int = Field(ge=1)
    satisfied: bool
    evidence: str = Field(min_length=1)


class Review(StrictModel):
    verdict: Literal["pass", "revise", "needs_evidence"]
    issues: list[Issue]
    scores: Scores
    strengths: list[str]
    remaining: list[str]
    editorial_checks: list[EditorialCheck] = Field(default_factory=list)


class BriefReviewResult(StrictModel):
    editorial_checks: list[EditorialCheck] = Field(min_length=1)
    review: Review


class Probe(StrictModel):
    ok: bool


class SourceSnapshot(StrictModel):
    source_id: str
    source_url: str | None
    author: str | None
    license: License
    attribution: str | None
    text_path: str
    text: str
    info_as_of: str | None
    collected_at: str | None
    edition: str | None
    sha256: str
    source_kind: Literal['original', 'user_summary'] = 'original'
    original_sources: list[OriginalSource] = Field(default_factory=list)

    @model_serializer(mode='wrap')
    def serialize_fact_metadata(self, handler):
        return fact_metadata(handler(self))


class CallEvent(StrictModel):
    stage: str
    model: str
    response_model: str
    effort: str
    prompt_version: str
    prompt_sha256: str
    usage: dict
    elapsed_sec: float
    at: str


class Generator(StrictModel):
    model: str
    effort: str
    prompt_version: str
    revision: int = Field(ge=0, le=2)
    feedback_revision: int = Field(default=0, ge=0, strict=True)
    self_check_model: str
    revision_model: str
    self_check_effort: str
    calls: list[CallEvent]
    revision_prompt_version: str | None = None


class Duration(StrictModel):
    estimated_sec: float
    measured_sec: float | None
    method: str
    chars_per_sec: float


class ReviewRecord(Review):
    feedback_round_revision: int | None = Field(default=None, ge=0, strict=True)
    feedback_revision: int | None = Field(default=None, ge=0, strict=True)
    revision: int


class Handoff(StrictModel):
    schema_version: Literal['1.0.0']
    content_id: str
    created_at: str
    updated_at: str | None = None
    title: str
    category: CategoryCode
    format: str
    production_type: ProductionType
    tags: list[str]
    version: str
    target_sec: int
    script: list[str]
    sources: list[SourceSnapshot] = Field(min_length=1)
    material: Material
    claims: list[Claim]
    adaptation_note: AdaptationNote
    license: str
    attribution: str | None
    source_url: str | None
    generator: Generator
    status: Literal['ready_for_downstream_review', 'hold']
    rights_status: Literal['restricted', 'unverified', 'metadata_supplied_not_legal_approval', 'facts_only_reviewed_not_legal_approval', 'attributed_news_reviewed_not_legal_approval']
    use_assessment: UseAssessment | None = None
    release_status: Literal['pending_content_review', 'pending_processing_review', 'pending_editorial_approval', 'blocked'] | None = None
    rights_notes: list[str] = Field(default_factory=list)
    input_sha256: str
    self_review: list[ReviewRecord]
    editorial_brief: list[str] = Field(default_factory=list)
    unresolved: list[str]
    duration: Duration
    fixture: bool
    input_metadata: SourceMeta
    downstream_approval: Literal['pending']
    tts_checked: Literal[False]
