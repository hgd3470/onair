"""Keep article-processing evidence inside the collector, outside the handoff."""
from datetime import datetime, timezone
from script_agent.fact_source import is_independent_fact_source
from script_agent.models import SourceMeta
from script_agent.rights import resolve_rights
from script_agent.use_assessment import UseAssessment, fingerprint
from .core import digest


def apply_private_assessment(out, raw, evidence):
    if not is_independent_fact_source(out['input_metadata']) or 'use_assessment' in out:
        raise ValueError('independent_fact_handoff_required')
    assessment=UseAssessment.model_validate(raw)
    _,_,rights=resolve_rights(SourceMeta.model_validate(out['input_metadata']))
    out.update(status='hold',rights_status=rights,release_status='pending_content_review')
    review=assessment.content
    required={s['source_url'] for s in evidence['provenance']['original_sources']}
    urls=[p.source_url for p in assessment.processing]
    valid=(assessment.content_sha256==fingerprint(out)
           and assessment.evidence_sha256==digest(evidence) and assessment.evidence_verified
           and rights=='metadata_supplied_not_legal_approval'
           and bool(required) and required<=set(urls) and len(urls)==len(set(urls))
           and review.verdict=='pass' and review.facts_only and review.independent_wording
           and review.no_distinctive_expression and not review.findings
           and sorted(review.covered_sentences)==list(range(1,len(out['script'])+1)))
    permissions=[getattr(p,op) for p in assessment.processing for op in ('fetch','store','model_transfer')]
    restricted=any(s['license'].get('commercial') is False or s['license'].get('modify') is False
                   for s in evidence['provenance']['original_sources'])
    if restricted or any(p.status=='prohibited' for p in permissions):
        out.update(rights_status='restricted',release_status='blocked')
        return out
    if not valid:return out
    out['rights_status']='facts_only_reviewed_not_legal_approval'
    quality=bool(out['self_review']) and out['self_review'][-1]['verdict']=='pass' and not out['unresolved']
    if not quality:return out
    out['status']='ready_for_downstream_review'
    now=datetime.now(timezone.utc)
    permitted=all(p.status=='permitted' and datetime.fromisoformat(p.reviewed_at)<=now<datetime.fromisoformat(p.valid_until)
                  for p in permissions)
    out['release_status']='pending_editorial_approval' if permitted else 'pending_processing_review'
    return out
