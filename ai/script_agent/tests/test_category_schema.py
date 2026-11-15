import json

import httpx
import pytest
from pydantic import ValidationError

from script_agent.io import atomic_json
from script_agent.models import (Handoff, Material, RevisionResult, SourceMeta,
                                 category_response_schema)
from script_agent.providers import APIProvider, ProviderError
from test_pipeline import fixture, flow_draft, setup_pipeline


CODES = [f'C{i:02}' for i in range(1, 22)]


@pytest.mark.parametrize('category', ['C21 성경·신앙', 'C00', 'C22', 'c21', ''])
def test_category_labels_and_invalid_codes_are_rejected(category):
    with pytest.raises(ValidationError):
        Material.model_validate({**fixture()['material'], 'category': category})
    with pytest.raises(ValidationError):
        SourceMeta(category=category)


def test_all_category_contracts_publish_the_same_enum():
    assert Material.model_json_schema()['properties']['category']['enum'] == CODES
    assert Handoff.model_json_schema()['properties']['category']['enum'] == CODES
    choices = SourceMeta.model_json_schema()['properties']['category']['anyOf']
    assert choices == [{'enum': CODES, 'type': 'string'}, {'type': 'null'}]
    for code in CODES:
        assert Material.model_validate({**fixture()['material'], 'category': code}).category == code
    assert SourceMeta().category is None


def category_field(schema, stage):
    if stage == 'revise':
        ref = next(x['$ref'] for x in schema['properties']['material']['anyOf'] if '$ref' in x)
        schema = schema['$defs'][ref.rsplit('/', 1)[1]]
    return schema['properties']['category']


def response_body(provider, value):
    raw = json.dumps(value, ensure_ascii=False)
    if provider == 'gemini':
        return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': raw}]}}]}
    return {'status': 'completed', 'output': [{'content': [{'type': 'output_text', 'text': raw}]}]}


@pytest.mark.parametrize('provider', ['gemini', 'openai'])
@pytest.mark.parametrize('stage', ['analyze', 'revise'])
@pytest.mark.parametrize('returned_category', ['C21', 'C21 성경·신앙', 'C20'])
def test_actual_provider_request_and_validation_lock_category(
        tmp_path, monkeypatch, provider, stage, returned_category):
    pipe, c, draft, _, _ = setup_pipeline(tmp_path, [])
    current = {**c['material'], 'category': 'C21'}
    returned = {**current, 'category': returned_category}
    payload = {'metadata': {'category': 'C21'}, 'material': current}
    value = returned if stage == 'analyze' else {'draft': draft.model_dump(), 'material': returned}
    sent = []

    def post(*args, **kwargs):
        sent.append(kwargs['json'])
        return httpx.Response(200, json=response_body(provider, value))

    monkeypatch.setattr(httpx, 'post', post)
    settings = {**pipe.config['models']['generator'], 'provider': provider}
    pipe.config['models']['generator'] = settings
    pipe.providers['generator'] = APIProvider(settings, {provider + '_api_key': 'fake-test-key'})
    model = Material if stage == 'analyze' else RevisionResult
    args = (stage, payload, model, tmp_path/'logs', current['production_type'])
    if returned_category == 'C21':
        result = pipe.call(*args)
        material = result if stage == 'analyze' else result.material
        assert material.category == 'C21'
    else:
        with pytest.raises(ProviderError, match='invalid_structured_output'):
            pipe.call(*args)
        assert not list((tmp_path/'logs').glob('*-result.json'))
    assert len(sent) == 1
    schema = (sent[0]['generationConfig']['responseJsonSchema'] if provider == 'gemini'
              else sent[0]['text']['format']['schema'])
    assert category_field(schema, stage)['enum'] == ['C21']
    saved = json.loads((tmp_path/'logs'/f'01-{stage}-request.json').read_text())
    assert category_field(saved['response_schema'], stage)['enum'] == ['C21']


def test_missing_input_category_allows_inference_but_revision_locks_it(tmp_path):
    pipe, c, draft, generator, _ = setup_pipeline(tmp_path, [])
    # Inspect the schemas at the pipeline boundary, with no input category.
    captured = []
    original = generator.complete

    def complete(system, payload, schema):
        captured.append(schema.model_json_schema())
        return original(system, payload, schema)

    generator.complete = complete
    material = pipe.call('analyze', {'metadata': {}}, Material, tmp_path/'logs')
    pipe.call('revise', {'metadata': {}, 'material': material.model_dump()},
              RevisionResult, tmp_path/'logs', material.production_type)
    assert category_field(captured[0], 'analyze')['enum'] == CODES
    assert category_field(captured[1], 'revise')['enum'] == [material.category]


def test_revision_null_material_remains_allowed():
    model = category_response_schema(RevisionResult, 'C21')
    result = model.model_validate({'draft': flow_draft().model_dump(), 'material': None})
    assert result.material is None


@pytest.mark.parametrize('old_schema', [None, {'type': 'object'}])
def test_changed_or_missing_checkpoint_schema_requires_new_destination(tmp_path, old_schema):
    pipe, c, _, generator, _ = setup_pipeline(tmp_path, [])
    payload = {'metadata': c['metadata']}
    pipe.call('analyze', payload, Material, tmp_path/'logs')
    request = tmp_path/'logs/01-analyze-request.json'
    saved = json.loads(request.read_text())
    if old_schema is None:
        saved.pop('response_schema')
    else:
        saved['response_schema'] = old_schema
    atomic_json(request, saved)
    pipe.events = []
    with pytest.raises(ValueError, match='checkpoint_config_changed_use_new_destination'):
        pipe.call('analyze', payload, Material, tmp_path/'logs')
    assert generator.calls == 1
