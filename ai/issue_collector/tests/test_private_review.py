import copy
import pytest
from test_publication import output
from issue_collector.core import digest
from issue_collector.private_review import apply_private_assessment
from issue_collector.use_review import protected,reusable_record
from script_agent.use_assessment import UseAssessment,ContentReview,SourceProcessing,fingerprint
from script_agent.models import Handoff


@pytest.fixture
def private_case(output):
    out=copy.deepcopy(output)
    license={'kind':'CC0-1.0','version':'1.0','commercial':True,'modify':True,'share_alike':False,
        'url':'https://creativecommons.org/publicdomain/zero/1.0/','evidence':'자체 작성 사실 소스에 대한 사용자 공개 선언'}
    for source in [out['input_metadata'],out['sources'][0]]:
        source.update(source_id='ICF-20261111-issue-test',source_kind='original',source_url=None,
                      license=license,author='이슈 수집 에이전트',attribution='')
        source.pop('original_sources',None)
    out.update(license='CC0-1.0',source_url=None,attribution='',unresolved=[])
    out['self_review']=[{'revision':0,'verdict':'pass','issues':[],
        'scores':{'grounding':5,'listening':5,'engagement':5,'type_fit':5},'strengths':[],'remaining':[]}]
    evidence={'provenance':{'original_sources':[{'source_url':'https://private.example/article','license':{'kind':'unknown'}}]}}
    assessment=UseAssessment(content_sha256=fingerprint(out),evidence_sha256=digest(evidence),
        evidence_report='collector-private/evidence.json',reviewed_at='2026-11-12T00:00:00+09:00',reviewer='fixture',
        evidence_verified=True,content=ContentReview(verdict='pass',facts_only=True,independent_wording=True,
        no_distinctive_expression=True,covered_sentences=list(range(1,len(out['script'])+1)),findings=[],rationale='fixture'),
        processing=[SourceProcessing(source_url='https://private.example/article',article_sha256='a'*64)]).model_dump()
    return out,assessment,evidence


def test_private_article_audit_sets_state_without_entering_handoff(private_case):
    out,assessment,evidence=private_case
    before=copy.deepcopy(out)
    apply_private_assessment(out,assessment,evidence)
    Handoff.model_validate(out)
    assert out['status']=='ready_for_downstream_review'
    assert out['release_status']=='pending_processing_review'
    assert protected(out)==protected(before)
    assert 'use_assessment' not in out
    assert 'private.example' not in str(out)
    assert 'original_sources' not in str(out)
    record={'content_sha256':fingerprint(out),'preserved_sha256':digest(protected(out)),
        'evidence_sha256':digest(evidence),**{k:out[k] for k in ('status','rights_status','release_status')}}
    assert reusable_record(record,out,evidence,assessment)==record


@pytest.mark.parametrize('change',['content','evidence','coverage','findings','missing_source','self_review'])
def test_private_review_cannot_approve_changed_or_incomplete_evidence(private_case,change):
    out,assessment,evidence=private_case
    if change=='content':out['script'][0]='내용이 바뀌었습니다.'
    if change=='evidence':evidence['changed']=True
    if change=='coverage':assessment['content']['covered_sentences']=[]
    if change=='findings':assessment['content']['findings']=['수정 필요']
    if change=='missing_source':assessment['processing'][0]['source_url']='https://other.example/a'
    if change=='self_review':out['self_review'][-1]['verdict']='revise'
    apply_private_assessment(out,assessment,evidence)
    assert out['status']=='hold' and out['release_status']=='pending_content_review'


def test_internal_article_restriction_remains_blocked(private_case):
    out,assessment,evidence=private_case
    evidence['provenance']['original_sources'][0]['license']['commercial']=False
    assessment['evidence_sha256']=digest(evidence)
    apply_private_assessment(out,assessment,evidence)
    assert out['status']=='hold' and out['release_status']=='blocked'
