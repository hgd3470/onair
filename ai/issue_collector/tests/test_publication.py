import copy
import json
from pathlib import Path

import pytest
from script_agent.models import Handoff, SourceMeta
from script_agent.news_context import HEADER
from script_agent.use_assessment import fingerprint, apply_use_assessment
from issue_collector.core import digest, read, save
from issue_collector.publication import (NOTES, apply_output_license, publish_demo,
                                         verify_publication_export)
from issue_collector.use_review import protected, reusable_record


@pytest.fixture
def output():
    # Committed, self-authored fixture; no saved demos, credentials or API needed.
    agent = Path(__file__).resolve().parents[2]/'script_agent'
    case = read(agent/'examples/reviewer_cases/explanation.json')
    text = HEADER+'\n'+case['source_text']
    meta = SourceMeta.model_validate(case['metadata'] | {
        'author':'이슈 수집 에이전트', 'source_kind':'user_summary', 'license':{'kind':'unknown'},
        'original_sources':[{'source_url':'https://example.org/article','author':'시험 매체'}]})
    out = {
        'schema_version':'1.0.0','content_id':'S2-test','created_at':'2026-11-12T00:00:00+09:00',
        'version':'1.0.2','category':meta.category,'production_type':'사실해설형',
        'target_sec':30,'material':case['material'],
        **{k:v for k,v in case['draft'].items() if k!='changes'},
        'license':'unknown','attribution':'입력 원문 출처','source_url':'https://example.org/article',
        'rights_status':'unverified','rights_notes':['source.license.kind: 미확인'],
        'status':'hold','release_status':'pending_content_review',
        'input_metadata':meta.model_dump(),'input_sha256':'a'*64,
        'sources':[{'source_id':'test','source_url':meta.source_url,'author':meta.author,
            'license':meta.license.model_dump(),'attribution':meta.attribution,'text':text,
            'text_path':'source.txt','info_as_of':None,'collected_at':None,'edition':None,
            'sha256':digest(text),'source_kind':meta.source_kind,
            'original_sources':[x.model_dump() for x in meta.original_sources]}],
        'generator':{'model':'fixture','effort':'medium','prompt_version':'v21','revision':2,
            'feedback_revision':1,'self_check_model':'fixture','revision_model':'fixture',
            'self_check_effort':'medium','calls':[]},
        'self_review':[],'editorial_brief':[],
        'duration':{'estimated_sec':30,'measured_sec':None,'method':'fixture','chars_per_sec':8.3},
        'fixture':True,'downstream_approval':'pending','tts_checked':False,
    }
    Handoff.model_validate(out)
    return out


@pytest.mark.parametrize('handoff_version', [5, 6])
def test_publication_preserves_source_linked_fact_credit(output, tmp_path, handoff_version):
    from test_opinions import make_source, draft_material
    from script_agent.models import Fact, Claim
    _, text, meta, _, _ = make_source(tmp_path, opinions=False)
    text = text.replace('원문 인계 v6\n', f'원문 인계 v{handoff_version}\n', 1)
    evidence = json.loads(next(line[len('근거 F1: '):] for line in text.splitlines()
                               if line.startswith('근거 F1: ')))
    draft, material = draft_material([])
    material.facts = [Fact(id='f1', text='제도 변경 발표', quote=evidence[0]['quote'],
                           kind='source_statement', conditions=['F1'])]
    draft.script = ['가일보에 따르면 정부가 제도 변경을 발표했습니다.']
    draft.claims = [Claim(sentence=1, kind='fact', fact_ids=['f1'])]
    output.update({k: v for k, v in draft.model_dump().items() if k != 'changes'})
    output.update(material=material.model_dump(), category=meta.category, input_metadata=meta.model_dump())
    output['sources'][0].update(text=text, sha256=digest(text))
    # v6 reviews fingerprint the credit; publication must preserve that reviewed value.
    if handoff_version == 6:
        output['attribution'] = '가일보'
    before = copy.deepcopy(output)
    after = apply_output_license(output)
    assert after['attribution'] == '가일보'
    assert after['license'] == 'All rights reserved'
    assert after['sources'] == before['sources'] and output == before
    assert fingerprint(after) == fingerprint(before)


def test_only_existing_top_level_policy_fields_change(output):
    before=copy.deepcopy(output)
    after=apply_output_license(output)
    assert output==before  # Pure adapter; callers control persistence.
    assert set(after)==set(before)
    assert {k for k in before if before[k]!=after[k]}=={'license','attribution','rights_notes'}
    assert after['license']=='All rights reserved' and after['attribution']==''
    assert after['rights_notes']==before['rights_notes']+list(NOTES)
    assert fingerprint(after)==fingerprint(before)
    assert apply_output_license(after)==after
    Handoff.model_validate(after)


@pytest.mark.parametrize('status,rights,release',[
    ('hold','unverified','pending_content_review'),
    ('hold','restricted','blocked'),
    ('ready_for_downstream_review','facts_only_reviewed_not_legal_approval','pending_processing_review'),
])
def test_publication_never_grants_source_rights_or_approval(output,status,rights,release):
    output.update(status=status,rights_status=rights,release_status=release)
    output['input_metadata']['original_sources'][0]['license']['commercial']=False
    after=apply_output_license(output)
    assert (after['status'],after['rights_status'],after['release_status'])==(status,rights,release)
    for key in ('input_metadata','sources','self_review','generator','downstream_approval','tts_checked'):
        assert after[key]==output[key]


@pytest.mark.parametrize('change',['author','original','reading','unmarked'])
def test_other_generator_outputs_are_not_relicensed(output,change):
    if change=='author':output['input_metadata']['author']='다른 수집기'
    if change=='original':output['input_metadata']['source_kind']='original'
    if change=='reading':output['production_type']='원문낭독형'
    if change=='unmarked':output['sources'][0]['text']='일반 입력'
    with pytest.raises(ValueError,match='collector_generated_script_required'):
        apply_output_license(output)


def prepare_demo(tmp_path,output,with_report=True):
    demo=tmp_path/'demo/test'
    save(demo/'scripts/issue/test/out.json',output)
    save(demo/'manifest.json',{'scripts':[{'id':'test','stream':'issue','status':output['status'],
        'output':'scripts/issue/test/out.json'}]})
    if with_report:
        record={'id':'test','stream':'issue','preserved_sha256':digest(protected(output)),
                'record':'use-review/records/test','content_sha256':fingerprint(output)}
        save(demo/'use-review/verification.json',{'scripts':[record]})
        save(demo/'use-review/records/test/before.json',output)
    return demo


def test_migration_preserves_model_records_and_supports_exact_overlay(tmp_path,output):
    demo=prepare_demo(tmp_path,output)
    original_review=(demo/'use-review/records/test/before.json').read_bytes()
    manifest=(demo/'manifest.json').read_bytes()
    assert publish_demo(tmp_path,'test')==0
    after=read(demo/'scripts/issue/test/out.json')
    record=read(demo/'use-review/verification.json')['scripts'][0]
    verify_publication_export(demo,record,output,after)
    assert record['content_sha256']==fingerprint(after)
    assert record['preserved_sha256']==digest(protected(after))
    assert (demo/'use-review/records/test/before.json').read_bytes()==original_review
    assert (demo/'manifest.json').read_bytes()==manifest
    assert publish_demo(tmp_path,'test')==0
    corrupted=copy.deepcopy(after);corrupted['script'][0]='임의 수정'
    with pytest.raises(ValueError,match='publication_snapshot_mismatch'):
        verify_publication_export(demo,record,output,corrupted)
    corrupted=copy.deepcopy(after);corrupted['rights_notes'].append('근거 없는 허가')
    with pytest.raises(ValueError,match='publication_snapshot_mismatch'):
        verify_publication_export(demo,record,output,corrupted)


def test_migration_without_review_keeps_hold(tmp_path,output):
    demo=prepare_demo(tmp_path,output,False)
    publish_demo(tmp_path,'test')
    after=read(demo/'scripts/issue/test/out.json')
    assert after['status']=='hold'
    assert 'use_assessment' not in after


def test_migration_refuses_stale_report(tmp_path,output):
    demo=prepare_demo(tmp_path,output)
    report=read(demo/'use-review/verification.json');report['scripts'][0]['preserved_sha256']='invalid'
    save(demo/'use-review/verification.json',report)
    with pytest.raises(ValueError,match='publication_review_stale'):publish_demo(tmp_path,'test')
    assert read(demo/'scripts/issue/test/out.json')==output


def test_interrupted_migration_resumes_exact_planned_values(tmp_path,output,monkeypatch):
    import issue_collector.publication as module
    demo=prepare_demo(tmp_path,output)
    real=module.save
    def interrupted(path,value):
        if str(path).endswith('use-review/verification.json'):raise OSError('interrupted')
        real(path,value)
    monkeypatch.setattr(module,'save',interrupted)
    with pytest.raises(OSError):publish_demo(tmp_path,'test')
    assert (demo/'publication/pending.json').exists()
    assert read(demo/'scripts/issue/test/out.json')['license']=='All rights reserved'
    monkeypatch.setattr(module,'save',real)
    assert publish_demo(tmp_path,'test')==0
    assert not (demo/'publication/pending.json').exists()
    record=read(demo/'use-review/verification.json')['scripts'][0]
    verify_publication_export(demo,record,output,read(demo/'scripts/issue/test/out.json'))


def test_review_recalculation_does_not_remove_publication_policy(output):
    from script_agent.use_assessment import UseAssessment,ContentReview,SourceProcessing
    output['use_assessment']=UseAssessment(content_sha256=fingerprint(output),evidence_sha256='c'*64,
        evidence_report='evidence.json',reviewed_at='2026-11-12T00:00:00+09:00',reviewer='fixture',
        evidence_verified=True,content=ContentReview(verdict='pass',facts_only=True,independent_wording=True,
        no_distinctive_expression=True,covered_sentences=list(range(1,len(output['script'])+1)),
        findings=[],rationale='fixture'),processing=[SourceProcessing(source_url='https://example.org/article',
        article_sha256='d'*64)]).model_dump()
    apply_use_assessment(output)
    after=apply_output_license(output)
    assert apply_use_assessment(copy.deepcopy(after))==after
    evidence={'fixture':True}
    after['use_assessment']['evidence_sha256']=digest(evidence)
    record={'content_sha256':fingerprint(after),'preserved_sha256':digest(protected(after)),
        'evidence_sha256':digest(evidence),**{k:after[k] for k in ('status','rights_status','release_status')}}
    assert reusable_record(record,after,evidence)==record


def test_feedback_source_metadata_reset_is_reexported_without_counter_changes(output):
    # script_agent feedback rebuilds source rights/attribution; the collector
    # adapter restores only the publication declaration after that operation.
    published=apply_output_license(output)
    revised=copy.deepcopy(published)
    revised['attribution']=output['attribution'];revised['rights_notes']=output['rights_notes']
    revised['generator']['feedback_revision']=2
    revised['version']='1.0.4'
    after=apply_output_license(revised)
    assert after['attribution']=='' and after['rights_notes']==published['rights_notes']
    assert after['version']=='1.0.4' and after['generator']==revised['generator']


def test_old_cc0_declaration_is_replaced_without_relabeling_articles(output):
    output['license']='CC0-1.0'
    output['rights_notes'].append('[collector-script-cc0-v1] 출처 없이 상업 이용 허용')
    after=apply_output_license(output)
    assert after['license']=='All rights reserved'
    assert any('© 틈(TEUM) 팀' in n for n in after['rights_notes'])
    assert all(not n.startswith('[collector-script-cc0-v1]') for n in after['rights_notes'])
    assert after['input_metadata']==output['input_metadata'] and after['sources']==output['sources']
    assert after['status']==output['status'] and fingerprint(after)==fingerprint(output)


def test_old_pending_publication_policy_is_not_resumed(tmp_path,output):
    demo=prepare_demo(tmp_path,output,False)
    path='publication/legacy.json'
    save(demo/path, {'policy':'collector-script-cc0-v1', 'writes':[
        {'path':'scripts/issue/test/out.json','before':output,'after':dict(output,license='CC0-1.0')}]})
    save(demo/'publication/pending.json', {'transaction':path})
    with pytest.raises(ValueError,match='publication_policy_changed'):
        publish_demo(tmp_path,'test')
    assert read(demo/'scripts/issue/test/out.json')==output


def test_standalone_collector_and_generator_declare_same_policy(output):
    from issue_collector import copyright as collector
    from script_agent import publication as agent
    assert (collector.POLICY, collector.LICENSE, collector.NOTES)==(agent.POLICY, agent.LICENSE, agent.NOTES)
    after=agent.apply_team_copyright(copy.deepcopy(output))
    assert after['license']=='All rights reserved'
    assert fingerprint(after)==fingerprint(output)
    assert after['status']==output['status'] and after['sources']==output['sources']
