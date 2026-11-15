import copy
import json
from pathlib import Path
import pytest
from script_agent.models import Handoff
from script_agent.use_assessment import (UseAssessment, ContentReview, SourceProcessing,
                                         Permission, fingerprint, apply_use_assessment)
from issue_collector.use_review import evidence_for, protected
from issue_collector.core import digest

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def sample():
    # Repository demo is deliberately not required in CI.
    from script_agent.models import SourceMeta
    meta=SourceMeta.model_validate({'source_kind':'user_summary', 'production_type':'사실해설형',
        'original_sources':[{'source_url':'https://example.com/a'}]})
    return {'title':'정부 발표', 'script':['정부가 새 기준을 발표했습니다.'], 'claims':[], 'material':{},
            'input_sha256':'a'*64,'input_metadata':meta.model_dump(),'production_type':'사실해설형',
            'sources':[{'text':'정부 발표 원문','sha256':'b'*64,'text_path':'old.txt'}],
            'self_review':[{'verdict':'pass'}],'unresolved':[], 'status':'hold','rights_status':'unverified'}


def attach(out):
    out['use_assessment']=UseAssessment(content_sha256=fingerprint(out), evidence_sha256='c'*64,
        evidence_report='audit/evidence.json',reviewed_at='2026-11-12T00:00:00+00:00',reviewer='test',
        evidence_verified=True,content=ContentReview(verdict='pass',facts_only=True,independent_wording=True,
        no_distinctive_expression=True,covered_sentences=[1],findings=[],rationale='독립적 사실 전달'),
        processing=[SourceProcessing(source_url='https://example.com/a',article_sha256='d'*64)]).model_dump()
    return out


def test_unknown_license_is_not_content_hold(sample):
    old=copy.deepcopy(sample);attach(sample);apply_use_assessment(sample)
    assert sample['status']=='ready_for_downstream_review'
    assert sample['rights_status']=='facts_only_reviewed_not_legal_approval'
    assert sample['release_status']=='pending_processing_review'
    assert protected(sample)==protected(old)


def test_legacy_behavior_unchanged(sample):
    old=copy.deepcopy(sample);apply_use_assessment(sample);assert sample==old


@pytest.mark.parametrize('field,value',[('script',['새로운 주장입니다.']),('title','바뀐 제목'),
    ('claims',[{'new':'claim'}]),('material',{'new':'fact'}),('self_review',[{'verdict':'revise'}])])
def test_edited_content_invalidates(sample,field,value):
    attach(sample);sample[field]=value;apply_use_assessment(sample)
    assert sample['status']=='hold'
    assert sample['release_status']=='pending_content_review'


@pytest.mark.parametrize('change',['missing_source','duplicate_source','missing_sentence','duplicate_sentence',
                                    'evidence_false','not_facts','expression','findings','verdict'])
def test_incomplete_assessment_cannot_bypass(sample,change):
    attach(sample);a=sample['use_assessment']
    if change=='missing_source':a['processing'][0]['source_url']='https://example.com/other'
    if change=='duplicate_source':a['processing']*=2
    if change=='missing_sentence':a['content']['covered_sentences']=[]
    if change=='duplicate_sentence':a['content']['covered_sentences']=[1,1]
    if change=='evidence_false':a['evidence_verified']=False
    if change=='not_facts':a['content']['facts_only']=False
    if change=='expression':a['content']['independent_wording']=False
    if change=='findings':a['content']['findings']=['표현 대응 확인 필요']
    if change=='verdict':a['content']['verdict']='needs_evidence'
    apply_use_assessment(sample);assert sample['status']=='hold'


def permission(status='permitted'):
    return Permission(status=status,basis='상업 브리핑의 이 작업에 대한 계약',evidence='contract/001',
        reviewed_by='rights-editor',reviewed_at='2020-01-01T00:00:00+00:00',valid_until='2099-01-01T00:00:00+00:00').model_dump()


def test_processing_grants_do_not_publish(sample):
    attach(sample)
    for op in ('fetch','store','model_transfer'):sample['use_assessment']['processing'][0][op]=permission()
    apply_use_assessment(sample)
    assert sample['status']=='ready_for_downstream_review'
    assert sample['release_status']=='pending_editorial_approval'


def test_processing_prohibition_blocks(sample):
    attach(sample);sample['use_assessment']['processing'][0]['model_transfer']=permission('prohibited')
    apply_use_assessment(sample);assert sample['release_status']=='blocked';assert sample['status']=='hold'


def test_expired_permission_is_pending(sample):
    attach(sample)
    for op in ('fetch','store','model_transfer'):
        sample['use_assessment']['processing'][0][op]=permission()|{'valid_until':'2021-01-01T00:00:00+00:00'}
    apply_use_assessment(sample);assert sample['release_status']=='pending_processing_review'


def test_no_evidence_cannot_claim_permission():
    with pytest.raises(ValueError):Permission(status='permitted')


def test_explicit_restriction_not_silently_overridden(sample):
    sample['input_metadata']['original_sources'][0]['license']['commercial']=False
    attach(sample);apply_use_assessment(sample)
    assert sample['status']=='hold';assert sample['release_status']=='blocked'


def test_reading_original_cannot_use_exception(sample):
    sample['production_type']='원문낭독형';sample['input_metadata']['production_type']='원문낭독형'
    attach(sample);apply_use_assessment(sample);assert sample['status']=='hold'


def test_paths_do_not_invalidate_content(sample):
    attach(sample);sample['sources'][0]['text_path']='relocated.txt';apply_use_assessment(sample)
    assert sample['status']=='ready_for_downstream_review'


@pytest.mark.parametrize('changed',[False, True])
def test_reassessment_cannot_erase_processing_prohibition(changed):
    from issue_collector.use_review import retain_processing_decisions
    old=SourceProcessing(source_url='https://example.com/a',article_sha256='a'*64).model_dump()
    old['fetch']=permission()
    old['model_transfer']=permission('prohibited')
    fresh=SourceProcessing(source_url=old['source_url'],article_sha256=('b' if changed else 'a')*64)
    retain_processing_decisions([fresh],[old])
    assert fresh.model_transfer.status=='prohibited'
    assert fresh.fetch.status==('unknown' if changed else 'permitted')


def test_scoped_review_reuses_only_current_complete_assessment(sample):
    from issue_collector.use_review import reusable_record
    attach(sample);apply_use_assessment(sample)
    evidence={'verified':'facts'}
    sample['use_assessment']['evidence_sha256']=digest(evidence)
    record={'content_sha256':fingerprint(sample),'preserved_sha256':digest(protected(sample)),
            'evidence_sha256':digest(evidence),**{k:sample[k] for k in ('status','rights_status','release_status')}}
    assert reusable_record(record,sample,evidence)==record
    with pytest.raises(ValueError,match='stale'):
        reusable_record(record,sample,{'verified':'different facts'})
    altered=copy.deepcopy(sample);altered['script']=['변경된 문장입니다.']
    with pytest.raises(ValueError,match='stale'):
        reusable_record(record,altered,evidence)
    # An expired decision or manually stale workflow state cannot silently be retained.
    altered=copy.deepcopy(sample);altered['release_status']='pending_editorial_approval'
    with pytest.raises(ValueError,match='stale'):
        reusable_record(record,altered,evidence)


@pytest.mark.parametrize('release',['pending_processing_review','blocked'])
def test_validation_hold_preserves_content_and_processing_decisions(tmp_path,monkeypatch,sample,release):
    from issue_collector.use_review import record_validation_hold, ScriptValidationError
    from script_agent.models import Issue
    # This fixture focuses on the mutable gate; full Handoff validation is covered
    # by generation integration tests using real schema objects.
    monkeypatch.setattr(Handoff,'model_validate',lambda value:value)
    attach(sample);sample['release_status']=release
    before=copy.deepcopy(sample)
    item={'id':'example','stream':'issue'}
    exc=ScriptValidationError([Issue(sentence=1,problem='문장 40자 초과',reason='41자',direction='분리')])
    record=record_validation_hold(tmp_path,tmp_path/'audit',tmp_path/'out.json',item,sample,exc)
    assert protected(sample)==protected(before)
    assert sample['use_assessment']==before['use_assessment']
    assert sample['status']=='hold'
    assert sample['release_status']==('blocked' if release=='blocked' else 'pending_content_review')
    assert record['facts_review']=='not_run' and record['checks']==exc.problems
    assert item['facts_review']=='not_run'
    assert (tmp_path/'audit/validation-failures/issue/example.json').exists()


def test_assess_demo_continues_after_invalid_script(tmp_path,monkeypatch,sample):
    from issue_collector import use_review, publication
    from issue_collector.core import save,read
    from script_agent.models import Issue
    demo=tmp_path/'demo/case';scripts=[]
    for name in ['first','second']:
        value=copy.deepcopy(sample)
        path=f'scripts/issue/{name}/out.json'
        save(demo/path,value)
        scripts.append({'id':name,'stream':'issue','status':'hold','output':path})
    save(demo/'manifest.json',{'collection_run':'collected','target_date':'2026-11-12','scripts':scripts})
    save(demo/'use-review/verification.json',{'scripts':[{'id':'second','stream':'issue','sentinel':'preserve'}]})
    monkeypatch.setattr(publication,'publish_demo',lambda *a:None)
    monkeypatch.setattr(use_review,'credentials',lambda *a:{})
    monkeypatch.setattr(use_review,'Models',lambda *a:None)
    monkeypatch.setattr(Handoff,'model_validate',lambda value:value)
    visited=[]
    def evidence(run,demo,item,out):
        visited.append(item['id'])
        if item['id']=='first':
            raise use_review.ScriptValidationError([Issue(sentence=1,problem='문장 오류',reason='검사 실패',direction='수정')])
        raise RuntimeError('second evidence reached')
    monkeypatch.setattr(use_review,'evidence_for',evidence)
    with pytest.raises(RuntimeError,match='second evidence reached'):
        use_review.assess_demo(tmp_path,{},'case')
    assert visited==['first','second']
    assert any(x.get('sentinel')=='preserve' for x in read(demo/'use-review/verification.json')['scripts'])
    saved=read(demo/'manifest.json')['scripts'][0]
    assert saved['status']=='hold' and saved['facts_review']=='not_run'
    assert protected(read(demo/saved['output']))==protected(sample)
