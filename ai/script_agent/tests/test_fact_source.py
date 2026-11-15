import copy
import pytest
from script_agent.models import SourceMeta,SourceSnapshot,Handoff,License
from script_agent.fact_source import fact_metadata
from script_agent.rights import resolve_rights
from script_agent.io import atomic_json
from script_agent.pipeline import parse_feedback
from test_pipeline import setup_pipeline,review,flow_draft,ROOT


def fact_meta():
    return SourceMeta(source_id='ICF-20261111-issue-test',author='이슈 수집 에이전트',
        source_kind='original',attribution='',license=License(kind='CC0-1.0',
        evidence='사용자 공개 방침에 따른 독립 사실 소스의 CC0 선언'))


def assert_no_articles(value):
    if isinstance(value,dict):
        assert 'original_sources' not in value
        for v in value.values():assert_no_articles(v)
    elif isinstance(value,list):
        for v in value:assert_no_articles(v)


def test_cc0_source_does_not_require_attribution_but_requires_evidence():
    resolved,notes,status=resolve_rights(fact_meta())
    assert status=='metadata_supplied_not_legal_approval'
    assert resolved.attribution==''
    assert resolved.license.commercial and resolved.license.modify
    assert resolved.license.share_alike is False
    assert_no_articles(resolved.model_dump())
    missing=fact_meta();missing.license.evidence=None
    assert resolve_rights(missing)[2]=='unverified'
    limited=fact_meta();limited.license.commercial=False
    assert resolve_rights(limited)[2]=='restricted'
    conflicting=fact_meta();conflicting.license.url='https://example.org/not-cc0'
    assert resolve_rights(conflicting)[2]=='unverified'


@pytest.mark.parametrize('field,value',[
    ('original_sources',[{'source_url':'https://article.example/a'}]),
    ('source_url','https://article.example/a'),('source_kind','user_summary'),
])
def test_fact_input_rejects_article_metadata_before_api(field,value):
    raw=fact_meta().model_dump();raw[field]=value
    with pytest.raises(ValueError,match='private_article_metadata_in_fact_source'):
        SourceMeta.model_validate(raw).model_dump()


def test_old_summary_contract_is_unchanged():
    meta=SourceMeta(source_kind='user_summary')
    assert 'original_sources' in meta.model_dump()
    assert resolve_rights(meta)[2]=='unverified'


def test_generation_and_feedback_requests_and_output_never_reintroduce_articles(tmp_path):
    changed=flow_draft()
    pipe,c,_,generator,reviewer=setup_pipeline(tmp_path,[review('pass'),review('pass')],revisions=[changed])
    meta=SourceMeta.model_validate(c['metadata']|fact_meta().model_dump())
    meta.production_type='창작·각색형';meta.category=c['material']['category'];meta.target_sec=c['metadata']['target_sec']
    out=pipe.run(c['source_text'],meta,'e'*64,tmp_path/'out')
    assert out['license']=='CC0-1.0' and out['attribution']==''
    assert out['status']=='ready_for_downstream_review'
    assert_no_articles(out)
    assert_no_articles(Handoff.model_validate(out).model_dump())
    path=tmp_path/'out'/f"{out['content_id']}.json"
    revised=pipe.apply_feedback(path,parse_feedback(ROOT/'examples/feedback.ko.json'),out['version'])
    assert revised['license']=='CC0-1.0' and revised['attribution']==''
    assert_no_articles(revised)
    assert_no_articles(generator.payloads)
    assert_no_articles(reviewer.payloads)
    assert_no_articles(SourceSnapshot.model_validate(revised['sources'][0]).model_dump())


def test_reserved_rights_fact_source_and_legacy_cc0_remain_distinct():
    meta=fact_meta()
    meta.license=License(kind='All rights reserved', commercial=True, modify=True, share_alike=False,
        evidence='팀 내부 제작 허용. 외부 이용은 별도 허락 필요.')
    meta.attribution='© 틈(TEUM) 팀. All rights reserved.'
    resolved,notes,status=resolve_rights(meta)
    assert status=='metadata_supplied_not_legal_approval'
    assert resolved.model_dump()['license']['kind']=='All rights reserved'
    assert fact_meta().model_dump()['license']['kind']=='CC0-1.0'
    raw=meta.model_dump();raw['original_sources']=[{'source_url':'https://example.org/article'}]
    with pytest.raises(ValueError,match='private_article_metadata_in_fact_source'):
        fact_metadata(raw)
