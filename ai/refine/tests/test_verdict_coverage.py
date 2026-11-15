import unittest
from teum_refine.llm import review
class Coverage(unittest.TestCase):
 def test_missing_retry_then_review(self):
  class Fake:
   prompt_style='xml-ko'
   def __init__(self,recover):self.recover=recover;self.stages=[]
   def call(self,stage,payload,schema,instruction,system=None):
    self.stages.append(stage)
    if stage=='R3-b':return {'claims':[dict(id='c1',sent=1,text='사실',kind='event',checkable=True)]}
    if stage.startswith('R3-d'):return {'verdicts':[dict(claim_id='c1',verdict='supported',evidence='f1',note='일치')] if self.recover and stage.endswith('retry') else []}
    if stage=='R4':return dict(entities=[],driving=[],harmful=[],injection=[])
    if stage=='R4c':return {'items':[]}
    if stage=='R5a':return {**{k:dict(score=5,reason='양호',sents=[1]) for k in ('listening','structure','style','tts')},'hard_sents':[]}
    if stage.startswith('R5b'):return {**{f'q{i}':dict(answer=True,reason='양호',sents=[1]) for i in range(1,6)},'overall_fun':3,'overall_reason':'무난'}
  d=dict(production_type='사실해설',category='life',script=['사실'],material={'facts':[{'id':'f1','text':'사실'}]},sources=[])
  for recover in (True,False):
   c=Fake(recover);items,_,_=review(d,c);self.assertEqual(c.stages.count('R3-d-retry'),1);self.assertEqual(any(x['problem']=='판정 누락' and x['decision']=='human_review' for x in items),not recover)
