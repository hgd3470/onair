import pytest
from issue_collector.demo import demo_selection,describe_demo


def collection():
    return {'streams':{s:{'items':[{'id':f'{s}-{n}'} for n in range(3)]} for s in ('issue','market')}}


def test_single_issue_uses_existing_generation_path_without_market():
    run=collection();del run['streams']['market']
    assert demo_selection(run,['issue'],1)==[('issue',{'id':'issue-0'})]
    assert describe_demo([{'stream':'issue','status':'hold'}])=='이슈 1편 + 대본 1편'


def test_default_six_and_explicit_market_scope():
    assert len(demo_selection(collection(),['issue','market'],3))==6
    assert demo_selection(collection(),['market','market'],1)==[('market',{'id':'market-0'})]
    rows=[{'stream':s,'status':'hold'} for s,_ in demo_selection(collection(),['issue','market'],3)]
    assert describe_demo(rows)=='이슈 3편 + 국내 시장 3편 + 대본 6편'
    rows[0]['status']='failed'
    assert describe_demo(rows).endswith('대본 5편')


@pytest.mark.parametrize('streams,count,code',[
    (['issue'],0,'count_out_of_range'),(['issue'],21,'count_out_of_range'),
    ([],1,'invalid_demo_streams'),(['other'],1,'invalid_demo_streams'),
    (['issue'],4,'insufficient_collected_items'),
])
def test_invalid_scope_fails_before_model_calls(streams,count,code):
    with pytest.raises(ValueError,match=code):demo_selection(collection(),streams,count)
