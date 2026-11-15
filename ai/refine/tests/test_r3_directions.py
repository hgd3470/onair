import unittest
from teum_refine.llm import CLAIMS,VERDICTS,r3_fix,review

class R3Directions(unittest.TestCase):
 def test_verdict_directions(self):
  facts=[{'id':'f1','conditions':['상한 이내','수령 거절']},{'id':'f10','conditions':['무관한 조건']}]
  cases={'contradicted':'근거(f1)와 맞게 고치기','overstated':'빠진 조건·범위「상한 이내」를 해당 주장에 명시하기','not_found':'근거가 있는 내용만 남기거나 출처를 추가하기','outdated':'기준일을 밝히거나 최신 정보로 바꾸기','supported':''}
  for verdict,expected in cases.items():
   with self.subTest(verdict=verdict):self.assertEqual(r3_fix({'verdict':verdict,'evidence':'f1','relevant_conditions':['상한 이내']},facts),expected)
  self.assertEqual(r3_fix({'verdict':'overstated','evidence':'s1'},facts),'근거에 없는 세부「s1」를 삭제하거나 원문에 있는 내용으로 바꾸기')
 def test_r3_request_has_no_fix_instruction_or_schema(self):
  calls=[]
  class Fake:
   prompt_style='xml-ko'
   def call(self,stage,payload,schema,instruction,system=None):
    calls.append((stage,schema,instruction))
    if stage=='R3-b':return {'claims':[{'id':'c1','sent':1,'text':'주장','kind':'condition','checkable':True}]}
    if stage=='R3-d':return {'verdicts':[{'claim_id':'c1','verdict':'overstated','evidence':'f1','note':'조건 누락','relevant_conditions':['조건']}]}
    raise RuntimeError('end of fixture')
  items,raw,_=review({'production_type':'사실해설','script':['주장'],'sources':[],'material':{'facts':[{'id':'f1','text':'근거','conditions':['조건']}]}},Fake())
  for stage,schema,instruction in calls[:2]:
   self.assertNotIn('fix',str(schema));self.assertNotIn('fix',instruction)
  self.assertEqual(items[0]['direction'],'빠진 조건·범위「조건」를 해당 주장에 명시하기')
  for schema in (CLAIMS,VERDICTS):self.assertNotIn('fix',str(schema))
