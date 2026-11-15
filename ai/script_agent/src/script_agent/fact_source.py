"""Existing-schema boundary for independently authored collector facts."""
FACTS_HEADER = '이슈 수집 에이전트 · 사실 인계 v4'
FACT_SOURCE_PREFIX = 'ICF-'
FACT_AUTHOR = '이슈 수집 에이전트'


def is_independent_fact_source(value):
    get = value.get if isinstance(value, dict) else lambda key, default=None: getattr(value,key,default)
    return str(get('source_id') or '').startswith(FACT_SOURCE_PREFIX) and get('author') == FACT_AUTHOR


def fact_metadata(data):
    """Drop the optional empty field only for this explicit input contract.

    Reject attached source articles instead of silently deleting their evidence.
    Other inputs keep their original metadata/serialization contract.
    """
    if is_independent_fact_source(data):
        if (data.get('original_sources') or data.get('source_url')
                or data.get('source_kind') != 'original'
                or data.get('license',{}).get('kind') not in {'CC0-1.0', 'All rights reserved'}):
            raise ValueError('private_article_metadata_in_fact_source')
        data = {k:v for k,v in data.items() if k != 'original_sources'}
    return data
