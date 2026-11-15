"""Material card IDs are not collector opinion IDs, including in API schemas."""
import pytest
from pydantic import ValidationError
from script_agent.models import Fact, Material, RevisionResult, category_response_schema


@pytest.mark.parametrize('identifier',['o1','O1','F1','f0','f01','f1\n'])
def test_opinion_card_rejects_collector_or_malformed_id(identifier):
    with pytest.raises(ValidationError):
        Fact(id=identifier,text='전망',quote='원문의 전망',kind='opinion',conditions=['O1'])


@pytest.mark.parametrize('model',[Material,RevisionResult])
def test_analysis_and_revision_api_schemas_require_f_ids(model):
    schema=category_response_schema(model,'C16').model_json_schema()
    assert schema['$defs']['Fact']['properties']['id']['pattern']==r'^f[1-9][0-9]*$'
    assert Fact(id='f6',text='전망',quote='원문의 전망',kind='opinion',conditions=['O1']).id=='f6'
