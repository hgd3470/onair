from script_agent.io import atomic_json
from script_agent.models import SourceMeta, OriginalSource, License, Handoff
from script_agent.pipeline import parse_feedback
from script_agent.use_assessment import UseAssessment, ContentReview, SourceProcessing, fingerprint, apply_use_assessment
from test_pipeline import ROOT, setup_pipeline, review, flow_draft


def test_external_revision_invalidates_facts_review_without_resetting_counters(tmp_path):
    changed=flow_draft()
    changed.script[0]='돈 이야기로 사이가 틀어질 뻔했어요.'
    pipe,c,_,_,_=setup_pipeline(tmp_path,[review('pass'),review('pass')],revisions=[changed])
    meta=SourceMeta.model_validate(c['metadata'])
    meta.source_kind='user_summary'
    meta.original_sources=[OriginalSource(source_url='https://example.org/article',license=License())]
    out=pipe.run(c['source_text'],meta,'f'*64,tmp_path/'out')
    out['use_assessment']=UseAssessment(content_sha256=fingerprint(out),evidence_sha256='a'*64,
        evidence_report='audit/evidence.json',reviewed_at='2026-11-12T00:00:00+00:00',reviewer='test',
        evidence_verified=True,content=ContentReview(verdict='pass',facts_only=True,independent_wording=True,
            no_distinctive_expression=True,covered_sentences=list(range(1,len(out['script'])+1)),findings=[],rationale='fixture'),
        processing=[SourceProcessing(source_url='https://example.org/article',article_sha256='b'*64)]).model_dump()
    apply_use_assessment(out)
    assert out['status']=='ready_for_downstream_review'
    path=tmp_path/'out'/f"{out['content_id']}.json"
    atomic_json(path,out)
    revised=pipe.apply_feedback(path,parse_feedback(ROOT/'examples/feedback.ko.json'),out['version'])
    Handoff.model_validate(revised)
    assert revised['script'][0]==changed.script[0]
    assert revised['self_review'][-1]['verdict']=='pass'
    assert revised['status']=='hold'
    assert revised['release_status']=='pending_content_review'
    assert revised['use_assessment']==out['use_assessment']
    assert revised['generator']['revision']==out['generator']['revision']
    assert revised['generator']['feedback_revision']==1
