"""Selection/provenance and HTML embedding tests; no API or external inputs."""
import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

def module(name):
    spec=importlib.util.spec_from_file_location(name, ROOT/'tools'/f'{name}.py')
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj


def test_select_one_per_category_without_moving_input(tmp_path):
    root=tmp_path/'agent';(root/'input').mkdir(parents=True)
    (root/'config.toml').write_text((ROOT/'config.toml').read_text())
    for code in ['C01','C06','C07']:
        for n in [2,1]:
            (root/'input'/f'{code}-{n}.md').write_text(f'---\nsource_id: {code}-{n}\ncategory: {code}\n---\n자체 작성 시험 문장입니다.\n')
    before={p.name:p.read_bytes() for p in (root/'input').iterdir()}
    batch=tmp_path/'batch';runner=module('review_batch')
    plan=runner.prepare(root,batch,['C06','C07'])
    assert [c['source_id'] for c in plan['cases']]==['C06-1','C07-1']
    assert before=={p.name:p.read_bytes() for p in (root/'input').iterdir()}
    assert runner.prepare(root,batch,['C06','C07'])==plan
    with pytest.raises(ValueError):runner.prepare(root,batch,['C01'])


def test_html_escapes_source_and_carries_failure_context(tmp_path):
    batch=tmp_path/'batch';batch.mkdir()
    case={'source_id':'self-written','category':'C06','input_sha256':'a'*64,
          'source_sha256':'b'*64,'input_path':'input/self.md','source_title':'fixture',
          'source_text':'</script><script>bad()</script>','input_metadata':{}}
    plan={'batch_id':'batch','created_at':'2026-11-08','categories':['C06'],
          'selection_rule':'test','config':{},'cases':[case]}
    (batch/'selection.json').write_text(json.dumps(plan))
    folder=batch/'cases/C06';folder.mkdir(parents=True)
    (folder/'run-status.json').write_text(json.dumps({'status':'failed','code':'fact_quote_not_in_source'}))
    builder=module('build_review_site');dest=tmp_path/'site';builder.build([batch],dest)
    html=(dest/'index.html').read_text()
    assert '</script><script>bad()' not in html
    embedded=html.split('<script id="review-data" type="application/json">')[1].split('</script>')[0]
    data=json.loads(embedded)
    assert data['cases'][0]['source_text']==case['source_text']
    assert data['cases'][0]['run_status']['status']=='failed'
    assert data['cases'][0]['output'] is None
    assert data['cases'][0]['review_key'].endswith(':incomplete:batch')
    assert 'C01' in data['pending_categories']
    with pytest.raises(ValueError):builder.build([batch,batch],tmp_path/'duplicate')


@pytest.mark.parametrize('categories', [[], ['C06', 'C06'], ['C22'], ['../outside']])
def test_invalid_categories_are_rejected_before_creating_plan(tmp_path, categories):
    with pytest.raises(ValueError, match='Categories must be unique'):
        module('review_batch').prepare(tmp_path, tmp_path/'batch', categories)
    assert not (tmp_path/'batch').exists()


def test_standalone_completed_review_without_source_id(tmp_path, monkeypatch):
    root = tmp_path/'agent'
    (root/'input').mkdir(parents=True)
    (root/'config.toml').write_text((ROOT/'config.toml').read_text())
    (root/'input/story.md').write_text('---\ncategory: C06\n---\n자체 작성 시험 문장입니다.\n')
    batch = tmp_path/'external-batch'
    runner = module('review_batch')
    plan = runner.prepare(root, batch, ['C06'])
    case = plan['cases'][0]
    assert case['source_id'].startswith('SRC-')
    folder = batch/'cases/C06'
    folder.mkdir(parents=True)
    filename = 'S2-' + case['input_sha256'][:16] + '.json'
    (folder/filename).write_text(json.dumps({'script': ['자체 작성 대본입니다.'], 'version': '1.0.0'}))
    (root/'review_ui').mkdir()
    (root/'review_ui/index.template.html').write_text((ROOT/'review_ui/index.template.html').read_text())
    builder = module('build_review_site')
    monkeypatch.setattr(builder, 'ROOT', root)
    destination = tmp_path/'site'
    builder.build([batch], destination)
    data = json.loads((destination/'feedback-context.json').read_text())
    assert data['cases'][0]['category_name'] == 'C06'
    assert data['cases'][0]['output_path'] == f'cases/C06/{filename}'
    assert data['cases'][0]['review_key'].startswith(case['source_id'] + ':')


def test_empty_review_build_does_not_write_broken_page(tmp_path):
    destination = tmp_path/'site'
    with pytest.raises(ValueError, match='No cases'):
        module('build_review_site').build([], destination)
    assert not destination.exists()


def test_partial_review_ignores_quote_alignment_audit_files(tmp_path):
    batch = tmp_path/'batch'; batch.mkdir()
    case = {'source_id':'fixture', 'category':'C06', 'input_sha256':'a'*64,
            'source_text':'자체 작성 원문', 'input_metadata':{}}
    (batch/'selection.json').write_text(json.dumps({'batch_id':'batch','created_at':'2026-11-08',
        'categories':['C06'],'selection_rule':'test','config':{},'cases':[case]}))
    logs = batch/'cases/C06/logs/id'; logs.mkdir(parents=True)
    draft = {'script':['정상 중간 대본입니다.']}
    (logs/'revision-1.json').write_text(json.dumps({'draft':draft}))
    (logs/'revision-4-quote-alignment.json').write_text(json.dumps([{'fact_id':'f1'}]))
    builder = module('build_review_site');builder.build([batch], tmp_path/'site')
    data = json.loads((tmp_path/'site/feedback-context.json').read_text())
    assert data['cases'][0]['partial_draft'] == draft


def test_editorial_brief_is_frozen_with_selection(tmp_path):
    root = tmp_path/'agent'; (root/'input').mkdir(parents=True)
    (root/'config.toml').write_text((ROOT/'config.toml').read_text())
    (root/'input/test.md').write_text('---\ncategory: C06\n---\n자체 작성 원문입니다.')
    runner = module('review_batch');batch = tmp_path/'batch'
    briefs = {'C06':['도입을 결말에서 회수한다.']}
    plan = runner.prepare(root, batch, ['C06'], briefs)
    assert plan['editorial_briefs'] == briefs
    assert runner.prepare(root, batch, ['C06'], briefs) == plan
    with pytest.raises(ValueError, match='new run directory'):
        runner.prepare(root, batch, ['C06'], {'C06':['다른 기준']})
    with pytest.raises(ValueError, match='invalid_editorial_brief'):
        runner.prepare(root, tmp_path/'bad', ['C06'], {'C07':['잘못된 대상']})
