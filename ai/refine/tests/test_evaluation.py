import csv,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from teum_refine.evaluation import evaluate,metrics
from teum_refine.llm import QUALITY,FUN
class Evaluation(unittest.TestCase):
 def test_required_named_shapes(self):
  self.assertEqual(set(QUALITY['required']),{'listening','structure','style','tts','hard_sents'})
  self.assertEqual(set(FUN['required']),{'q1','q2','q3','q4','q5','overall_fun','overall_reason'})
  for schema in (QUALITY,FUN):self.assertFalse(schema['additionalProperties'])
 def test_fixture(self):
  base=Path(__file__).parent/'fixtures/eval'
  class FakeClient:
   def __init__(self,*args,**kwargs):self.calls=[]
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as out, patch('teum_refine.evaluation.Client',FakeClient),patch('teum_refine.evaluation.review',return_value=([],{},[])),patch('teum_refine.__main__.safety',return_value=([],'<SAFE>')):
   rows,summary=evaluate(base/'cases',base/'truth.jsonl',out)
   byid={r['case_id']:r for r in rows}
   self.assertFalse(byid['original']['false_positive'])
   self.assertTrue(byid['number']['decision_detected'])
   self.assertTrue(byid['number']['stage_detected']) # sentence 1 within +/-1 of 2
   self.assertTrue(byid['harm']['stage_detected'])
   self.assertEqual(len(list(csv.DictReader((Path(out)/'eval_cases.csv').read_text().splitlines()))),3)
   self.assertEqual(json.loads((Path(out)/'cost.json').read_text())['cost_usd'],0)
   self.assertEqual({p.stem for p in Path(out).glob('*.refine.json')},{'original.refine','number.refine','harm.refine'})
 def test_stage_location_and_original_flag(self):
  t=dict(case_id='x',base_id=None,flaw='driving',expected_stage='R4',sent=3,expected_decision='revise')
  r=dict(decision='human_review',items=[dict(stage='R4-a',sent=2)])
  self.assertTrue(metrics(t,r)['stage_detected'])
  r['items'][0]['sent']=1
  self.assertFalse(metrics(t,r)['stage_detected'])
  t['flaw']='none';t['expected_stage']=None;r['decision']='pass_with_flags'
  self.assertFalse(metrics(t,r)['false_positive'])

 def test_kanana_assistant_role(self):
  from teum_refine.__main__ import safety_messages
  messages=safety_messages({'production_type':'사실해설','script':['대본 본문']})
  self.assertEqual(messages[1],{'role':'assistant','content':'대본 본문'})
  self.assertNotIn('대본 본문',messages[0]['content'])
 def test_ignore_only_decision(self):
  from teum_refine.rules import combine
  items=[{'stage':'R1','decision':'human_review','sent':0},{'stage':'R4','decision':'revise','sent':2}]
  t=dict(case_id='x',base_id=None,flaw='driving',sent=2,expected_stage='R4',expected_decision='revise')
  a=metrics(t,dict(items=items,decision=combine(items)))
  b=metrics(t,dict(items=items,decision=combine([x for x in items if x['stage']!='R1'])))
  self.assertEqual(a['fired_stages'],b['fired_stages'])
  self.assertEqual(a['stage_detected'],b['stage_detected'])
  self.assertEqual(b['decision'],'revise')
 def test_round1_review_items(self):
  from teum_refine.llm import review
  class Fake:
   def call(self,stage,payload,schema,instruction,system=None):
    if stage=='R3-b':return {'claims':[{'id':'c1','sent':1,'text':'주장','kind':'event','checkable':True}]}
    if stage=='R3-d':return {'verdicts':[{'claim_id':'c1','verdict':'not_found','evidence':'','note':'자료 없음'}]}
    if stage=='R4c':return {'items':[]}
    if stage=='R4':
     self.system=system
     return {'entities':[],'driving':[],'harmful':[{'sent':1,'reason':'위험행위 조장'}]}
    if stage=='R5a':return {**{k:{'score':5,'reason':'양호','sents':[1]} for k in ('listening','structure','style','tts')},'hard_sents':[{'sent':1,'reason':'긴 숫자열','severity':'major','kind':'multiple_numbers','fix':'숫자 정보를 나누기'}]}
    if stage.startswith('R5b'):return {**{f'q{i}':{'answer':True,'reason':'근거','sents':[1]} for i in range(1,6)},'overall_fun':3,'overall_reason':'보통'}
    if stage=='R2-llm':return {'items':[{'sent':1,'category':'money','reason':'원천 특정 금액 잔존','fix':'금액 바꾸기'}]}
    raise AssertionError(stage)
  d={'production_type':'사실해설','category':'life','script':['문장'],'material':{},'sources':[]}
  fake=Fake();items,_,_=review(d,fake)
  self.assertTrue(fake.system)
  self.assertTrue(any(x['stage']=='R3' and x['decision']=='pass_with_flags' and '근거 미제공' in x['reason'] for x in items))
  self.assertTrue(any(x['stage']=='R5a' and x['decision']=='revise' and x['sent']==1 for x in items))
  self.assertTrue(any(x['stage']=='R4' and x['decision']=='human_review' for x in items))
  d['production_type']='창작·각색';d['sources']=[{'source_id':'s','_text':'원천'}]
  items,_,_=review(d,Fake())
  self.assertTrue(any(x['stage']=='R2' and x['decision']=='revise' for x in items))
