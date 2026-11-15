"""Deterministic acceptance gate after independent GPT evidence review."""
import re
from .core import norm

def validate_facts(extraction, verification, articles, day, min_sources=2):
    by_id={a['id']:a for a in articles}
    counts={f.id:sum(x.id==f.id for x in extraction.facts) for f in extraction.facts}
    text_counts={norm(f.text):sum(norm(x.text)==norm(f.text) for x in extraction.facts) for f in extraction.facts}
    accepted=[]; rejected=[]
    for f in extraction.facts:
        reasons=[]
        reviews=[c for c in verification.checks if c.fact_id==f.id]
        if counts[f.id]!=1: reasons.append('duplicate_fact_id')
        if text_counts[norm(f.text)]!=1: reasons.append('duplicate_fact_text')
        if len(reviews)!=1: reasons.append('missing_or_duplicate_check')
        c=reviews[0] if len(reviews)==1 else None
        evidence_ids=set()
        for e in f.evidence:
            a=by_id.get(e.article_id)
            if not a or norm(e.quote) not in norm(a['text']):
                reasons.append('quote_not_in_fetched_article')
            else: evidence_ids.add(e.article_id)
        if f.kind in {'opinion','allegation'}: reasons.append('excluded_kind')
        if f.date_basis=='unknown': reasons.append('unknown_event_date')
        if f.kind!='background' and f.event_date!=day.isoformat(): reasons.append('wrong_event_date')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',f.event_date): reasons.append('invalid_event_date')
        if c:
            if c.verdict!='verified': reasons.append(c.verdict)
            if not c.date_confirmed: reasons.append('date_unconfirmed')
            if not c.attribution_correct: reasons.append('attribution_unconfirmed')
            supported=set(c.supported_article_ids)
            if not supported <= set(by_id): reasons.append('unknown_review_source')
            # Additional true corroboration must not invalidate two already quoted, supported originals.
            matched=supported & evidence_ids
            origins={by_id[x]['origin'] for x in matched}
            all_origins={by_id[x]['origin'] for x in supported if x in by_id}
            if len(origins & set(c.independent_origins))<min_sources: reasons.append('insufficient_independent_sources')
            if not set(c.independent_origins)<=all_origins: reasons.append('invented_independent_origin')
        if f.kind=='official_announcement' and not f.attribution.strip(): reasons.append('official_announcement_without_attribution')
        if f.involves_named_person and not f.attribution: reasons.append('named_person_without_attribution')
        # Long verbatim text is not our own wording. Short fixed factual phrases are unavoidable.
        compact=re.sub(r'\s+','',f.text)
        for a in articles:
            body=re.sub(r'\s+','',a['text'])
            if any(compact[i:i+60] in body for i in range(max(0,len(compact)-59))):
                reasons.append('long_verbatim_overlap'); break
        record={**f.model_dump(),'verification':c.model_dump() if c else None}
        if c and not reasons:
            record['evidence']=[e.model_dump() for e in f.evidence if e.article_id in set(c.supported_article_ids)]
        if reasons: rejected.append({**record,'reasons':sorted(set(reasons))})
        else: accepted.append(record)
    extras=set(c.fact_id for c in verification.checks)-set(counts)
    if extras:
        rejected += [{'id':x,'reasons':['unknown_review_fact_id']} for x in sorted(extras)]
    return accepted,rejected


def validate_opinions(extraction, verification, articles, facts):
    """Verify that an outlet expressed this view, not that its view is true."""
    indexed = {a['id']: a for a in articles}
    fact_ids = {f['id'] for f in facts}
    accepted, rejected = [], []
    for opinion in extraction.opinions:
        reasons = []
        article = indexed.get(opinion.article_id)
        reviews = [c for c in verification.opinion_checks if c.opinion_id == opinion.id]
        check = reviews[0] if len(reviews) == 1 else None
        if sum(o.id == opinion.id for o in extraction.opinions) != 1:
            reasons.append('duplicate_opinion_id')
        if sum(o.article_id == opinion.article_id and o.quote == opinion.quote for o in extraction.opinions) != 1:
            reasons.append('duplicate_opinion_quote')
        if not article or not opinion.quote.strip() or opinion.quote not in article['text']:
            reasons.append('opinion_quote_not_in_article')
        if not article or article.get('official') or opinion.attribution != article.get('publisher'):
            reasons.append('opinion_publisher_mismatch')
        if opinion.speaker and (not article or opinion.speaker not in article['text']):
            reasons.append('opinion_speaker_not_in_article')
        if not set(opinion.related_fact_ids) <= fact_ids:
            reasons.append('opinion_without_verified_context')
        if opinion.mode == 'direct' and opinion.text != opinion.quote:
            reasons.append('direct_quote_changed')
        if not check:
            reasons.append('missing_or_duplicate_opinion_check')
        elif (check.verdict != 'approved' or not check.attribution_correct
              or not check.context_preserved or not check.relevant):
            reasons.append('opinion_not_approved')
        if not reasons and len(accepted) >= 2:
            reasons.append('opinion_budget_exceeded')
        record = {**opinion.model_dump(), 'verification': check.model_dump() if check else None}
        if reasons:
            rejected.append({**record, 'reasons': sorted(set(reasons))})
        else:
            accepted.append(record)
    for check in verification.opinion_checks:
        if check.opinion_id not in {o.id for o in extraction.opinions}:
            rejected.append({'id': check.opinion_id, 'reasons': ['unknown_review_opinion_id']})
    return accepted, rejected
