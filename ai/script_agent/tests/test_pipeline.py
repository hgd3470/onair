import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from script_agent.io import atomic_json, load_config, read_source
from script_agent.models import Draft, Material, Review, SourceMeta
from script_agent.pipeline import Pipeline, parse_feedback
from script_agent.providers import Completion, ProviderError
from script_agent.queue import process_queue, retry_job
from script_agent.validation import checks, validate_material

ROOT = Path(__file__).resolve().parents[1]


def fixture(name='adaptation'):
    return json.loads((ROOT / 'examples/reviewer_cases' / (name+'.json')).read_text())


def flow_draft(name='adaptation'):
    # Adversarial reviewer fixtures remain unchanged. Control-flow tests need
    # a draft that passes the current deterministic editorial rules.
    draft = Draft.model_validate(fixture(name)['draft'])
    lines = {
        'adaptation': [
            '돈을 주려다 사이가 틀어질 뻔했어요.', '이 이야기는 가상 사연입니다.',
            '민수는 두 달간 친구에게 신세를 졌어요.', '이제 떠나며 월세를 나눠 내고 싶었죠.',
            '친구는 그 제안을 받아들이지 않았어요.', '민수는 돈부터 보내 놓을까 망설였어요.',
            '그러고는 연락을 막아 둘 생각도 했죠.', '하지만 둘 다 생각에만 그쳤습니다.',
            '친구의 속내는 아직 알 수 없어요.',
        ],
        'explanation': [
            '다음 독자가 기다리는 책도 연장될까요?', '가상 도서관의 규칙을 살펴볼게요.',
            '예약자가 있다면 더 빌릴 수는 없어요.', '그렇지 않은 책은 일주일 더 읽어요.',
            '다만 반납할 날이 지나기 전에 신청해요.', '신청 기회도 딱 한 번뿐입니다.',
            '처음 빌리는 기간은 열나흘이에요.', '빌린 다음 날부터 날짜를 세면 됩니다.',
        ],
    }[name]
    from script_agent.models import Claim
    ids = ([[], [], ['f1'], ['f1'], ['f1'], ['f2'], ['f2'], ['f3'], []]
           if name == 'adaptation' else [[], [], ['f4'], ['f5'], ['f3'], ['f3'], ['f2'], ['f2']])
    draft.script = lines
    draft.claims = [Claim(sentence=i, kind='source_statement' if facts else 'transition', fact_ids=facts)
                    for i, facts in enumerate(ids, 1)]
    return draft


class Fake:
    def __init__(self, values):
        self.values, self.calls = list(values), 0
        self.payloads = []
    def complete(self, system, payload, schema):
        self.calls += 1
        self.payloads.append(payload)
        value = self.values.pop(0)
        if isinstance(value, Exception): raise value
        if schema.__name__ == 'RevisionResult' and isinstance(value, Draft):
            # Let the requested schema validate the nested material like an API response.
            value = schema(draft=value, material=payload['material'])
        return Completion(value, {}, 0.0, 'fake-test-only')


def review(verdict='revise'):
    return Review.model_validate({'verdict':verdict, 'issues':[] if verdict=='pass' else [
        {'sentence':1,'problem':'source mismatch','reason':'considered is not performed','direction':'restore considered'}],
        'scores':{'grounding':2,'listening':4,'engagement':4,'type_fit':3},'strengths':[], 'remaining':[]})


def setup_pipeline(tmp_path, reviews, revisions=()):
    c = fixture()
    shutil.copytree(ROOT/'prompts',tmp_path/'prompts')
    config = load_config(ROOT)
    draft = flow_draft()
    material = Material.model_validate(c['material'])
    generator=Fake([material,draft,*revisions])
    reviewer=Fake(reviews)
    pipe=Pipeline(tmp_path,config,{},providers={'generator':generator,'reviewer':reviewer})
    return pipe,c,draft,generator,reviewer


def test_max_two_revisions_and_no_false_pass(tmp_path):
    draft=Draft.model_validate(fixture()['draft'])
    pipe,c,_,g,r=setup_pipeline(tmp_path,[review(),review(),review()],revisions=[draft,draft])
    out=pipe.run(c['source_text'],SourceMeta.model_validate(c['metadata']),'a'*64,tmp_path/'output')
    assert out['generator']['revision']==2
    assert out['generator']['feedback_revision']==0
    assert out['status']=='hold'
    assert g.calls==4 and r.calls==3
    assert out['generator']['revision_model']=='gemini-4-hydrogen'
    assert [(e['stage'],e['model'],e['effort']) for e in out['generator']['calls']]==[
        ('analyze','gemini-4-hydrogen','medium'),('generate','gemini-4-hydrogen','medium'),
        ('review','gpt-6-luna','medium'),('revise','gemini-4-hydrogen','medium'),
        ('review','gpt-6-luna','medium'),('revise','gemini-4-hydrogen','medium'),
        ('review','gpt-6-luna','medium')]
    assert g.payloads[2]['feedback']['issues'][0]['direction']=='restore considered'
    assert out['duration']['measured_sec'] is None
    assert out['sources'][0]['text']==c['source_text']
    assert out['downstream_approval']=='pending'


def test_saved_stages_resume_without_rebilling(tmp_path):
    pipe,c,draft,g,r=setup_pipeline(tmp_path,[ProviderError('openai','fixed','transport_or_timeout')])
    meta=SourceMeta.model_validate(c['metadata'])
    with pytest.raises(ProviderError): pipe.run(c['source_text'],meta,'b'*64,tmp_path/'out')
    pipe.providers['generator']=Fake([])
    pipe.providers['reviewer']=Fake([review('pass')])
    out=pipe.run(c['source_text'],meta,'b'*64,tmp_path/'out')
    assert pipe.providers['generator'].calls==0
    assert out['generator']['revision']==0


def test_external_feedback_has_separate_budget_after_internal_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("script_agent.pipeline.now", lambda: "2026-11-09T12:00:00+09:00")
    pipe,c,_,g,r=setup_pipeline(tmp_path,
        [review(),review(),review(),review('pass'),review('pass')],
        revisions=[flow_draft() for _ in range(4)])
    out=pipe.run(c['source_text'],SourceMeta.model_validate(c['metadata']),'c'*64,tmp_path/'out')
    assert out['generator']['revision'] == 2
    assert out['generator']['feedback_revision'] == 0
    path=tmp_path/'out'/f"{out['content_id']}.json"
    issues=parse_feedback(ROOT/'examples/feedback.ko.json')
    for count in (1, 2):
        out=pipe.apply_feedback(path,issues,out['version'])
        assert out['generator']['revision'] == 2
        assert out['generator']['feedback_revision'] == count
        assert out['version'] == f'1.0.{2 + count}'
        assert out['self_review'][-1]['revision'] == 2
        assert out['self_review'][-1]['feedback_revision'] == count
    before=path.read_bytes()
    calls=(g.calls,r.calls)
    with pytest.raises(ValueError,match='revision_budget_exhausted'):
        pipe.apply_feedback(path,issues,out['version'])
    assert path.read_bytes() == before
    assert (g.calls,r.calls) == calls


def test_legacy_feedback_counter_counts_one_round_despite_followup(tmp_path):
    pipe,c,_,_,_=setup_pipeline(tmp_path,
        [review('pass'),review(),review('pass')],
        revisions=[flow_draft(),flow_draft()])
    out=pipe.run(c['source_text'],SourceMeta.model_validate(c['metadata']),'d'*64,tmp_path/'out')
    del out['generator']['feedback_revision']
    path=tmp_path/'out'/f"{out['content_id']}.json"
    atomic_json(path,out)
    from script_agent.models import Handoff
    assert Handoff.model_validate(out).generator.feedback_revision == 0
    changed=pipe.apply_feedback(path,parse_feedback(ROOT/'examples/feedback.ko.json'),out['version'])
    assert changed['generator']['revision'] == 0
    assert changed['generator']['feedback_revision'] == 1
    assert changed['version'] == '1.0.1'
    assert [h['feedback_revision'] for h in changed['self_review'][1:]] == [1,1]
    assert [h['feedback_round_revision'] for h in changed['self_review'][1:]] == [1,2]


@pytest.mark.parametrize('counter', [-1, None, '0', '1', 0.0, 1.5, True, False])
def test_invalid_feedback_counter_is_rejected(tmp_path,counter):
    from script_agent.models import Handoff
    pipe,c,_,_,_=setup_pipeline(tmp_path,[review('pass')])
    out=pipe.run(c['source_text'],SourceMeta.model_validate(c['metadata']),'e'*64,tmp_path/'out')
    out['generator']['feedback_revision']=counter
    with pytest.raises(ValueError):
        Handoff.model_validate(out)


def test_reading_detects_rewrite_and_omission():
    c=fixture('reading')
    issues=checks(Draft.model_validate(c['draft']),Material.model_validate(c['material']),c['source_text'],SourceMeta.model_validate(c['metadata']))
    assert any(i.problem=='원문 불일치' for i in issues)


def test_fact_quote_must_be_exact():
    c=fixture('explanation')
    material=Material.model_validate(c['material'])
    material.facts[0].quote='없는 인용'
    with pytest.raises(ValueError,match='fact_quote_not_in_source'):
        validate_material(material,c['source_text'],SourceMeta.model_validate(c['metadata']))


def test_plain_source_and_metadata_not_required(tmp_path):
    path=tmp_path/'source.txt'; path.write_text('소스 하나입니다.')
    text,meta,digest=read_source(path)
    assert meta.license.kind=='unknown' and meta.production_type is None
    assert len(digest)==64 and text=='소스 하나입니다.'


def test_path_traversal_metadata_rejected(tmp_path):
    path=tmp_path/'source.md'; path.write_text('---\nsource_id: ../../other\n---\n원문')
    with pytest.raises(ValueError): read_source(path)


def test_symlink_input_rejected(tmp_path):
    real=tmp_path/'real.txt'; real.write_text('원문')
    link=tmp_path/'link.txt'; link.symlink_to(real)
    with pytest.raises(ValueError): read_source(link)


class QueuePipe:
    config={'pipeline':{'max_source_chars':60000}}
    def __init__(self, fail=False): self.calls=0; self.fail=fail
    def run(self,text,meta,digest,destination):
        self.calls+=1
        if self.fail: raise ProviderError('gemini','gemini-4-argon','api_error',429)
        out={'content_id':'S2-'+digest[:16],'input_sha256':digest,'status':'hold'}
        atomic_json(destination/(out['content_id']+'.json'),out)
        return out


def test_success_keeps_only_canonical_source_and_deduplicates(tmp_path):
    (tmp_path/'input').mkdir(); (tmp_path/'input/a.txt').write_text('원문')
    pipe=QueuePipe()
    first=process_queue(tmp_path,pipe)
    assert not (tmp_path/'input/a.txt').exists()
    assert not (tmp_path/'archive').exists()
    assert len(list((tmp_path/'state/completed').glob('*/job.json')))==1
    assert not list((tmp_path/'state/completed').rglob('*.txt'))
    (tmp_path/'input/b.txt').write_text('원문')
    second=process_queue(tmp_path,pipe)
    assert second[0]['duplicate'] and pipe.calls==1


def test_failure_preserves_source_then_retry(tmp_path):
    (tmp_path/'input').mkdir(); (tmp_path/'input/a.txt').write_text('원문')
    result=process_queue(tmp_path,QueuePipe(True))
    assert result[0]['http_status']==429
    archived=list((tmp_path/'failed').glob('*/a.txt'))[0]
    assert archived.read_text()=='원문'
    retry_job(tmp_path,archived.parent.name)
    assert process_queue(tmp_path,QueuePipe())[0]['status']=='completed'


def test_claim_interruption_recovers_sidecar(tmp_path):
    (tmp_path/'input').mkdir(); job=tmp_path/'processing/job1'; job.mkdir(parents=True)
    (job/'a.txt').write_text('원문')
    (tmp_path/'input/a.meta.json').write_text('{"category":"C04"}')
    atomic_json(job/'job.json',{'filename':'a.txt','status':'claiming'})
    assert process_queue(tmp_path,QueuePipe())[0]['status']=='completed'
    assert (tmp_path/'state/completed/job1/job.json').exists()
    assert not (tmp_path/'state/completed/job1/a.meta.json').exists()


def test_korean_feedback_keys():
    issue=parse_feedback(ROOT/'examples/feedback.ko.json')[0]
    assert issue.sentence==1 and issue.direction


def test_failed_calls_do_not_expose_server_error_body(monkeypatch):
    import httpx
    from script_agent.models import Probe
    from script_agent.providers import APIProvider
    secret='fake-secret-sentinel-not-real'
    monkeypatch.setattr(httpx,'post',lambda *a,**k:httpx.Response(400,json={'error':{'message':secret}}))
    p=APIProvider({'provider':'gemini','model':'gemini-4-argon','effort':'low','max_output_tokens':20},{'gemini_api_key':secret})
    with pytest.raises(ProviderError) as caught:
        p.complete('JSON',{},Probe)
    assert secret not in str(caught.value)
    assert secret not in json.dumps(caught.value.diagnostics)


def test_unknown_license_cannot_be_ready(tmp_path):
    pipe,c,_,_,_=setup_pipeline(tmp_path,[review('pass')])
    meta=SourceMeta.model_validate(c['metadata']); meta.license.kind='unknown'
    out=pipe.run(c['source_text'],meta,'d'*64,tmp_path/'out')
    assert out['status']=='hold' and out['rights_status']=='unverified'


def test_handoff_schema_and_portable_export(tmp_path):
    from script_agent.models import Handoff
    from script_agent.handoff import export_bundle
    pipe,c,_,_,_=setup_pipeline(tmp_path,[review('pass')])
    out=pipe.run(c['source_text'],SourceMeta.model_validate(c['metadata']),'e'*64,tmp_path/'out')
    Handoff.model_validate(out)
    result=export_bundle(tmp_path/'out',tmp_path/'bundle')
    assert result['count']==1
    source=out['sources'][0]
    assert not (tmp_path/'bundle/sources').exists()
    assert json.loads((tmp_path/'bundle'/f"{out['content_id']}.json").read_text())['sources'][0]['text']==source['text']


def test_revision_can_fix_card_conditions_from_source(tmp_path):
    from script_agent.models import RevisionResult
    c=fixture('explanation'); material=Material.model_validate(c['material'])
    material.facts[0].conditions=['대출 신청 1회마다']
    corrected=material.model_copy(deep=True); corrected.facts[0].conditions=['한 사람당 한 번에']
    draft=flow_draft('explanation')
    shutil.copytree(ROOT/'prompts',tmp_path/'prompts')
    config=load_config(ROOT)
    # This fixture tests card revisions, using its original timing calibration.
    config['pipeline'].update(chars_per_sec=4.5,character_count_mode='non_whitespace')
    pipe=Pipeline(tmp_path,config,{},providers={'generator':Fake([material,draft,RevisionResult(draft=draft,material=corrected)]),
      'reviewer':Fake([review(),review('pass')])})
    out=pipe.run(c['source_text'],SourceMeta.model_validate(c['metadata']),'f'*64,tmp_path/'out')
    assert out['material']['facts'][0]['conditions']==['한 사람당 한 번에']


def test_experiment_stops_at_deadline_before_model_call(tmp_path):
    from datetime import datetime, timedelta, timezone
    pipe,c,_,g,r=setup_pipeline(tmp_path,[])
    pipe.deadline=datetime.now(timezone.utc)-timedelta(seconds=1)
    with pytest.raises(ValueError,match='deadline'):
        pipe.run(c['source_text'],SourceMeta.model_validate(c['metadata']),'0'*64,tmp_path/'out')
    assert g.calls==0 and r.calls==0


def test_unquoted_yaml_date_is_supported(tmp_path):
    p=tmp_path/'date.md';p.write_text('---\ninfo_as_of: 2026-11-08\n---\n원문')
    assert read_source(p)[1].info_as_of=='2026-11-08'


def test_reading_without_edition_is_unverified():
    from script_agent.validation import rights_status
    meta=SourceMeta.model_validate(fixture('reading')['metadata']);meta.edition=None
    assert rights_status(meta)=='unverified'


@pytest.mark.parametrize('text',['https://example.org/source','[원문](https://example.org/source)'])
def test_link_only_input_requires_snapshot(tmp_path,text):
    path=tmp_path/'source.txt';path.write_text(text)
    with pytest.raises(ValueError,match='source_text_required'):
        read_source(path)


def test_queue_lock_blocks_second_worker(tmp_path):
    from script_agent.io import queue_lock
    with queue_lock(tmp_path):
        with pytest.raises(ValueError,match='queue_already_running'):
            with queue_lock(tmp_path): pass


def test_feedback_version_guard_prevents_stale_sentence_edits(tmp_path):
    pipe,c,_,_,reviewer=setup_pipeline(tmp_path,[review('pass')])
    out=pipe.run(c['source_text'],SourceMeta.model_validate(c['metadata']),'1'*64,tmp_path/'out')
    before=reviewer.calls
    with pytest.raises(ValueError,match='feedback_version_mismatch'):
        pipe.apply_feedback(tmp_path/'out'/f"{out['content_id']}.json",parse_feedback(ROOT/'examples/feedback.ko.json'),'1.0.1')
    assert reviewer.calls==before


def test_diagnostics_allow_only_known_codes():
    from script_agent.errors import safe_code
    assert safe_code(ValueError('fact_quote_not_in_source'))=='fact_quote_not_in_source'
    assert safe_code(ValueError('fake-secret-sentinel-not-real'))=='input_or_pipeline_error'


def test_thirty_execution_cap_prevents_more_calls(tmp_path):
    from script_agent.experiments import run_experiments
    pipe,c,_,g,r=setup_pipeline(tmp_path,[])
    from datetime import datetime, timedelta, timezone
    pipe.config['experiments']['deadline']=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    for i in range(30):
        (tmp_path/'experiments/runs'/f'{i:02d}'/'logs'/'S2-test').mkdir(parents=True)
    ledger=run_experiments(tmp_path,pipe,tmp_path/'unused',1)
    assert ledger['stopped']=='cap_or_deadline'
    assert g.calls==0 and r.calls==0


def test_external_feedback_uses_gemini_and_preserves_previous_calls(tmp_path):
    pipe,c,draft,g,r=setup_pipeline(tmp_path,[review('pass'),review('pass')],revisions=[flow_draft()])
    out=pipe.run(c['source_text'],SourceMeta.model_validate(c['metadata']),'2'*64,tmp_path/'out')
    path=tmp_path/'out'/f"{out['content_id']}.json"
    # Existing handoffs may still name the old GPT revision model.
    out['generator']['revision_model']='gpt-6-luna'
    atomic_json(path,out)
    previous_calls=list(out['generator']['calls'])
    issues=parse_feedback(ROOT/'examples/feedback.ko.json')
    changed=pipe.apply_feedback(path,issues,expected_version=out['version'])
    assert g.calls==3 and r.calls==2
    assert g.payloads[-1]['feedback']==[i.model_dump() for i in issues]
    assert changed['generator']['revision_model']=='gemini-4-hydrogen'
    assert changed['generator']['self_check_model']=='gpt-6-luna'
    assert changed['generator']['calls'][:len(previous_calls)]==previous_calls
    assert [(e['stage'],e['model']) for e in changed['generator']['calls'][-2:]]==[
        ('revise','gemini-4-hydrogen'),('review','gpt-6-luna')]
    assert changed['version']=='1.0.1'


def test_old_gpt_revision_checkpoint_is_not_reused(tmp_path):
    from script_agent.models import RevisionResult
    pipe,c,draft,g,r=setup_pipeline(tmp_path,[])
    pipe.providers['generator']=Fake([draft])
    log=tmp_path/'checkpoint'
    pipe.call('revise',{'material':c['material']},RevisionResult,log,c['metadata']['production_type'])
    request=log/'01-revise-request.json'
    saved=json.loads(request.read_text())
    saved['settings']=pipe.config['models']['reviewer']
    atomic_json(request,saved)
    pipe.events=[]
    pipe.providers['generator']=Fake([])
    with pytest.raises(ValueError,match='checkpoint_config_changed'):
        pipe.call('revise',{'material':c['material']},RevisionResult,log,c['metadata']['production_type'])
    assert pipe.providers['generator'].calls==0 and r.calls==0


@pytest.mark.parametrize('counter', [2, 3, 100])
def test_nonnegative_feedback_count_uses_budget_guard(tmp_path, counter):
    from script_agent.models import Handoff
    pipe,c,_,g,r=setup_pipeline(tmp_path,[review('pass')])
    out=pipe.run(c['source_text'],SourceMeta.model_validate(c['metadata']),'f'*64,tmp_path/'out')
    out['generator']['feedback_revision']=counter
    assert Handoff.model_validate(out).generator.feedback_revision == counter
    path=tmp_path/'out'/f"{out['content_id']}.json"
    atomic_json(path,out)
    before=path.read_bytes()
    calls=(g.calls,r.calls)
    with pytest.raises(ValueError,match='revision_budget_exhausted'):
        pipe.apply_feedback(path,parse_feedback(ROOT/'examples/feedback.ko.json'),out['version'])
    assert path.read_bytes() == before
    assert (g.calls,r.calls) == calls


def test_each_external_round_has_bounded_followups_and_counts_once(tmp_path):
    pipe,c,_,g,r=setup_pipeline(tmp_path,
        [review(),review(),review(),review(),review(),review(),review()],
        revisions=[flow_draft() for _ in range(6)])
    out=pipe.run(c['source_text'],SourceMeta.model_validate(c['metadata']),'7'*64,tmp_path/'out')
    path=tmp_path/'out'/f"{out['content_id']}.json"
    assert out['generator']['revision'] == 2
    issues=parse_feedback(ROOT/'examples/feedback.ko.json')
    for count in (1,2):
        calls=(g.calls,r.calls)
        out=pipe.apply_feedback(path,issues,out['version'])
        assert (g.calls-calls[0],r.calls-calls[1]) == (2,2)
        assert out['generator']['revision'] == 2
        assert out['generator']['feedback_revision'] == count
        assert out['version'] == f'1.0.{2+count}'
        assert out['status'] == 'hold'
        assert [h['feedback_revision'] for h in out['self_review'][-2:]] == [count,count]
        assert [h['feedback_round_revision'] for h in out['self_review'][-2:]] == [1,2]
    before=path.read_bytes()
    calls=(g.calls,r.calls)
    with pytest.raises(ValueError,match='revision_budget_exhausted'):
        pipe.apply_feedback(path,issues,out['version'])
    assert path.read_bytes() == before
    assert (g.calls,r.calls) == calls


@pytest.mark.parametrize('fail_at', ['revise','review'])
def test_failed_external_round_does_not_increment_or_overwrite(tmp_path,fail_at):
    pipe,c,_,g,r=setup_pipeline(tmp_path,[review('pass')])
    out=pipe.run(c['source_text'],SourceMeta.model_validate(c['metadata']),'8'*64,tmp_path/'out')
    path=tmp_path/'out'/f"{out['content_id']}.json"
    before=path.read_bytes()
    failure=ProviderError('mock','mock-model','mock-failure')
    g.values=[failure] if fail_at == 'revise' else [flow_draft()]
    r.values=[failure]
    with pytest.raises(ProviderError):
        pipe.apply_feedback(path,parse_feedback(ROOT/'examples/feedback.ko.json'),out['version'])
    assert path.read_bytes() == before
    assert json.loads(path.read_bytes())['generator']['feedback_revision'] == 0
