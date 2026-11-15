"""Editorial facts-only assessment is distinct from acquisition permissions.

This is a workflow gate, not a legal opinion or a publication authorization.
A report is issued by a trusted local reviewer; its fingerprint prevents accidental
reuse after content changes. It is not a cryptographic signature.
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Permission(Record):
    status: Literal['unknown', 'permitted', 'prohibited'] = 'unknown'
    basis: str = ''
    evidence: str = ''
    reviewed_by: str = ''
    reviewed_at: str = ''
    valid_until: str = ''

    @model_validator(mode='after')
    def evidence_required(self):
        if self.status != 'unknown':
            if not all(x.strip() for x in (self.basis, self.evidence, self.reviewed_by, self.reviewed_at, self.valid_until)):
                raise ValueError('permission_decision_requires_scope_evidence_reviewer_and_dates')
            for value in (self.reviewed_at, self.valid_until):
                if datetime.fromisoformat(value).tzinfo is None:
                    raise ValueError('permission_dates_require_timezone')
            if datetime.fromisoformat(self.valid_until) <= datetime.fromisoformat(self.reviewed_at):
                raise ValueError('permission_expiry_must_follow_review')
        return self


class SourceProcessing(Record):
    source_url: str = Field(min_length=1)
    article_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    fetch: Permission = Field(default_factory=Permission)
    store: Permission = Field(default_factory=Permission)
    model_transfer: Permission = Field(default_factory=Permission)


class ContentReview(Record):
    verdict: Literal['pass', 'revise', 'needs_evidence']
    facts_only: bool
    attributed_opinions: bool = False
    independent_wording: bool
    no_distinctive_expression: bool
    # Every sentence is considered, including title/bridges/attribution.
    covered_sentences: list[int]
    findings: list[str]
    rationale: str = Field(min_length=1)


class UseAssessment(Record):
    policy_version: Literal['facts-separation-v1', 'news-attribution-v2'] = 'facts-separation-v1'
    basis: Literal['independently_rewritten_facts', 'facts_with_optional_attributed_opinions'] = 'independently_rewritten_facts'
    content_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    evidence_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    evidence_report: str = Field(min_length=1)
    reviewed_at: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    evidence_verified: bool
    content: ContentReview
    processing: list[SourceProcessing] = Field(min_length=1)


def fingerprint(out):
    # Paths, workflow states and counters are not content. Source text, licenses,
    # claims and the final quality review are; edits to them invalidate this report.
    fields = ('title', 'script', 'claims', 'material', 'input_sha256',
              'input_metadata', 'production_type', 'self_review', 'unresolved')
    payload = {k: out[k] for k in fields}
    payload['sources'] = [{k: v for k, v in s.items() if k != 'text_path'} for s in out['sources']]
    if out['sources'][0]['text'].startswith('이슈 수집 에이전트 · 원문 인계 v6\n'):
        payload['attribution'] = out.get('attribution')
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def apply_use_assessment(out):
    """Recompute both gates without changing source metadata or editorial history."""
    raw = out.get('use_assessment')
    if raw is None:
        return out  # Preserve legacy contracts, including unknown-license holds.
    from .models import SourceMeta
    from .rights import resolve_rights
    assessment = UseAssessment.model_validate(raw)
    _, _, legacy = resolve_rights(SourceMeta.model_validate(out['input_metadata']).model_copy(
        update={'production_type': out['production_type']}))
    out['rights_status'] = legacy
    out['status'] = 'hold'
    out['release_status'] = 'pending_content_review'
    urls = [p.source_url for p in assessment.processing]
    required = {s['source_url'] for s in out['input_metadata']['original_sources'] if s.get('source_url')}
    review = assessment.content
    from .news_opinions import HEADER, opinion_checks, opinion_links, apply_news_attribution
    from .models import Draft, Material
    import copy
    opinion_content = False
    content_ok = review.facts_only
    if out['sources'][0]['text'].startswith(HEADER + '\n'):
        draft = Draft.model_validate({k: out[k] for k in ('title', 'format', 'tags', 'script', 'claims', 'adaptation_note', 'unresolved')} | {'changes': []})
        material = Material.model_validate(out['material'])
        text = out['sources'][0]['text']
        opinion_content = bool(opinion_links(draft, material, text))
        content_ok = (not opinion_checks(draft, material, text)[0]
                      and apply_news_attribution(copy.deepcopy(out))['attribution'] == out['attribution']
                      and ((not opinion_content and review.facts_only) or
                           (opinion_content and not review.facts_only and review.attributed_opinions
                            and assessment.policy_version == 'news-attribution-v2'
                            and assessment.basis == 'facts_with_optional_attributed_opinions')))
    valid = (assessment.content_sha256 == fingerprint(out) and assessment.evidence_verified
             and out['input_metadata']['source_kind'] == 'user_summary'
             and out['production_type'] != '원문낭독형' and bool(required)
             and required <= set(urls) and len(urls) == len(set(urls))
             and review.verdict == 'pass' and content_ok and review.independent_wording
             and review.no_distinctive_expression and not review.findings
             and sorted(review.covered_sentences) == list(range(1, len(out['script']) + 1)))
    permissions = [getattr(p, op) for p in assessment.processing for op in ('fetch', 'store', 'model_transfer')]
    if legacy == 'restricted' or any(p.status == 'prohibited' for p in permissions):
        out['release_status'] = 'blocked'
        return out
    if not valid:
        return out
    out['rights_status'] = ('attributed_news_reviewed_not_legal_approval' if opinion_content
                            else 'facts_only_reviewed_not_legal_approval')
    quality_pass = bool(out['self_review']) and out['self_review'][-1]['verdict'] == 'pass' and not out['unresolved']
    if quality_pass:
        out['status'] = 'ready_for_downstream_review'
    now = datetime.now(timezone.utc)
    permitted = all(p.status == 'permitted' and datetime.fromisoformat(p.reviewed_at) <= now
                    < datetime.fromisoformat(p.valid_until) for p in permissions)
    out['release_status'] = ('pending_content_review' if not quality_pass else
                             'pending_editorial_approval' if permitted else 'pending_processing_review')
    return out
