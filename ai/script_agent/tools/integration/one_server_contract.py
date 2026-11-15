"""Opt-in integration acceptance tests; requires the one-server branch.

The six base checks pass with one-server 0ba7bbe; d87e389 failed four.
This branch also checks two team-copyright handoffs (eight checks total).
They are not part of the standalone test suite.
Model calls are deterministic fixtures; real local Studio HTTP is exercised.
"""
import os
import pytest
import json
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_factory_e2e import studio
from test_pipeline import setup_pipeline, review, flow_draft, ROOT
from test_fact_source import fact_meta
from script_agent.models import SourceMeta, OriginalSource, License, Handoff
from script_agent.queue import process_queue
from script_agent.pipeline import parse_feedback
from script_agent.io import atomic_json, load_config
from script_agent.use_assessment import UseAssessment, ContentReview, SourceProcessing, fingerprint, apply_use_assessment
from teum_factory import steps
from teum_factory.config import Config
from teum_factory.state import State
from teum_factory.studio import Studio

RESULTS = Path(os.environ.get('TEUM_COMPAT_REPORT', '/tmp/teum-one-server-contract.jsonl'))
def record(name, **data):
    with RESULTS.open('a') as f:
        f.write(json.dumps(dict(name=name, **data), ensure_ascii=False)+'\n')


@pytest.mark.parametrize('license_kind', ['CC0-1.0', 'All rights reserved'])
@pytest.mark.parametrize('check', ['discovery', 'roundtrip'])
def test_real_queue_output_and_studio_roundtrip(studio, check, license_kind):
    cfg=Config.load();steps.ensure_layout(cfg)
    # Reuse deterministic model fixtures. The real queue, serializers, file layout,
    # HTTP upload and review ZIP are exercised. No external model/TTS calls.
    (cfg.sa_root/'prompts').unlink()
    pipe,c,_,_,_=setup_pipeline(cfg.sa_root,[review('pass')])
    meta=SourceMeta.model_validate(c['metadata']|fact_meta().model_dump())
    if license_kind == 'All rights reserved':
        meta.license=License(kind=license_kind, commercial=True, modify=True, share_alike=False,
            evidence='팀 내부 제작 허용. 외부 이용은 별도 허락 필요.')
        meta.attribution='© 틈(TEUM) 팀. All rights reserved.'
    meta.production_type=c['metadata']['production_type'];meta.category=c['material']['category'];meta.target_sec=c['metadata']['target_sec']
    src=cfg.sa_root/'input'/'facts.txt';src.write_text(c['source_text'])
    atomic_json(src.with_suffix('.meta.json'),meta.model_dump())
    results=process_queue(cfg.sa_root,pipe,limit=1)
    assert results[0]['status']=='completed',results
    output=next((cfg.sa_root/'output').rglob('S2-*.json'))
    data=json.loads(output.read_text());Handoff.model_validate(data)
    assert data['status']=='ready_for_downstream_review'
    state=State(cfg.work/'state.json');client=Studio(cfg.studio_url,cfg.token)
    discovered=steps.submit(cfg,state,client,False)
    assert output.parent.name=='_unbatched'
    record('factory_output_discovery',output=str(output.relative_to(cfg.sa_root)),generated=1,uploaded=len(discovered['uploaded']))
    if check == 'discovery':
        assert discovered['uploaded'] == [data['content_id'].lower()], 'Factory must discover nested generator outputs'
        return
    response=client.upload(output.name,output.read_bytes())
    assert response and all(r['ok'] for r in response),response
    cid=data['content_id'].lower();client.update(cid,'script_status','제출')
    kit=zipfile.ZipFile(io.BytesIO(client.review_kit()))
    restored=json.loads(kit.read('json/'+cid+'.json'))
    assert restored==data
    assert restored['license']==license_kind
    assert restored['input_metadata']['license']['kind']==license_kind
    assert restored['attribution']==meta.attribution
    assert 'original_sources' not in restored['sources'][0]
    from teum_refine.input import load,feedback_revision
    parsed=load(output)
    assert feedback_revision(parsed)==0
    assert parsed['sources'][0]['_text']==c['source_text']
    record('studio_refine_roundtrip',compatible=True,license=license_kind,null_source_url=True,article_metadata_absent=True,json_preserved=True)


def test_separate_feedback_budget(tmp_path):
    draft=flow_draft()
    pipe,c,_,_,_=setup_pipeline(tmp_path,[review(),review(),review('pass'),review('pass'),review('pass')],revisions=[draft,draft,draft,draft])
    output=pipe.run(c['source_text'],SourceMeta.model_validate(c['metadata']),'b'*64,tmp_path/'out')
    assert output['generator']['revision']==2 and output['status']=='ready_for_downstream_review'
    path=tmp_path/'out'/f"{output['content_id']}.json"
    for _ in range(2):
        output=pipe.apply_feedback(path,parse_feedback(ROOT/'examples/feedback.ko.json'),output['version'])
    assert output['generator']['revision']==2 and output['generator']['feedback_revision']==2
    assert output['version']=='1.0.4'
    from teum_refine.input import feedback_revision
    assert feedback_revision(output)==2
    record('feedback_budget',compatible=True,internal=2,external=2,version=output['version'])


def test_pending_processing_blocks_publication(studio):
    cfg=Config.load();steps.ensure_layout(cfg);(cfg.sa_root/'prompts').unlink()
    pipe,c,_,_,_=setup_pipeline(cfg.sa_root,[review('pass')])
    meta=SourceMeta.model_validate(c['metadata']);meta.source_kind='user_summary'
    meta.original_sources=[OriginalSource(source_url='https://example.org/article',license=License())]
    out=pipe.run(c['source_text'],meta,'c'*64,cfg.sa_root/'output')
    out['use_assessment']=UseAssessment(content_sha256=fingerprint(out),evidence_sha256='a'*64,evidence_report='fixture/audit.json',reviewed_at='2026-11-12T00:00:00+00:00',reviewer='test',evidence_verified=True,content=ContentReview(verdict='pass',facts_only=True,independent_wording=True,no_distinctive_expression=True,covered_sentences=list(range(1,len(out['script'])+1)),findings=[],rationale='test fixture'),processing=[SourceProcessing(source_url='https://example.org/article',article_sha256='b'*64)]).model_dump()
    apply_use_assessment(out);assert out['release_status']=='pending_processing_review'
    path=cfg.sa_root/'output'/f"{out['content_id']}.json";atomic_json(path,out)
    client=Studio(cfg.studio_url,cfg.token);state=State(cfg.work/'state.json')
    steps.submit(cfg,state,client,False)
    cid=out['content_id'].lower();assert client.scripts()[cid]['stage']==2
    published=[]
    mock=SimpleNamespace(scripts=lambda:{cid:{'stage':4,'state':'tts_done','stt':{'cer':0.0,'flag':False},'release_status':'pending_processing_review'}},add_to_app=lambda ids:published.extend(ids) or {'added':ids})
    cfg.auto_publish='own'
    steps.publish(cfg,state,mock,False)
    record('release_gate',release_status=out['release_status'],submitted=True,mock_publish_called=bool(published),real_audio_or_publish=False)
    assert not published, 'Pending processing review must prevent automatic publication'


def test_revised_hold_is_uploaded_for_human_review(tmp_path):
    root=tmp_path;(root/'output').mkdir();(root/'feedback').mkdir()
    path=root/'output'/'S2-case.json';path.write_text(json.dumps({'version':'1.0.0','status':'ready_for_downstream_review','content_id':'S2-case'}))
    (root/'feedback'/'s2-case.jsonl').write_text('{}')
    cfg=SimpleNamespace(sa_root=root,work=root,max_rounds=2,sa_cmd=['mock'],sa_env=lambda:root/'absent',repo=root,refine_label=lambda:'[test]')
    state=State(root/'state.json');state.note('s2-case',own=True,decision='revise',file=path.name,refined_sha='old',uploaded_sha='old')
    uploads=[];client=SimpleNamespace(update=lambda *a:None,comment=lambda *a:None,upload=lambda *a:uploads.append(a) or [{'ok':True,'msg':'s2-case accepted'}])
    def run(*a,**k):
        path.write_text(json.dumps({'version':'1.0.1','status':'hold','content_id':'S2-case'}));return 0,'',''
    with patch.object(steps,'run_cmd',run):steps.revise(cfg,state,client,False)
    steps.submit(cfg,state,client,False)
    record('revised_hold',latest_version='1.0.1',uploaded=bool(uploads))
    assert uploads, 'The latest hold revision must be visible to the human reviewer'


def test_pending_batch_resumes_without_new_inputs(tmp_path):
    root=tmp_path
    (root/'input').mkdir()
    (root/'processing'/'pending').mkdir(parents=True)
    atomic_json(root/'processing'/'pending'/'job.json',{'status':'processing','filename':'source.txt'})
    cfg=SimpleNamespace(sa_root=root,max_generate=5,generator_blocker=lambda:'',sa_cmd=['mock'],sa_env=lambda:root/'absent',repo=root)
    with patch.dict(os.environ, {'TEUM_SCRIPT_AGENT_CMD':'mock'}), patch.object(steps,'write_sa_config'), patch.object(steps,'run_cmd',return_value=(0,'[]','')) as runner:
        steps.generate(cfg,False)
    record('pending_batch_resume',pending_jobs=1,new_inputs=0,generator_called=bool(runner.call_count))
    assert runner.call_count == 1, 'A pending batch must resume even with an empty inbox'
