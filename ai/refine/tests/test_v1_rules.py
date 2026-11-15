import unittest
from decimal import Decimal
from teum_refine.numerals import extract,normalize,unmatched,evidence_tokens
from teum_refine.rules import check,license_checks,combine
class NewRules(unittest.TestCase):
 def test_korean_numerals(self):
  examples=[('일 명','1'),('구 개','9'),('십이 개','12'),('백오십 원','150'),('천사백사십삼 년','1443'),('오천만 원','50000000'),('일억 이천만 원','120000000'),('이조 삼억 원','2000300000000'),('영 퍼센트','0'),('공 점 오 퍼센트','0.5'),('삼 점 육일삼 퍼센트','3.613'),('한 달','1'),('두 시간','2'),('스물여덟 자','28'),('마흔두 살','42'),('일곱 번','7'),('열한 명','11'),('쉰 개','50')]
  for text,value in examples:
   with self.subTest(text=text):self.assertEqual([x['value'] for x in extract(text)],[Decimal(value)])
  self.assertEqual(normalize('십일월 육 일'),'11월 6일')
  for s in ['삼성 직원','일상 이야기','한동안 기다렸다','이 일이 좋다','만약 내일이면']:
   with self.subTest(ambiguous=s):self.assertEqual(extract(s),[])
 def test_exact_units_and_source(self):
  d={'material':{'facts':[{'label':'국고채 3년','text':'금리 3.613%, 0.056%포인트 감소'}]},'sources':[{'_text':'금액 이백오십만 원','info_as_of':'2026-11-06'}]};a=evidence_tokens(d)
  self.assertEqual(unmatched('삼 점 육일삼 퍼센트와 이백오십만 원',a),[])
  self.assertTrue(unmatched('삼 점 육일 퍼센트',a))
  self.assertTrue(unmatched('공 점 공오육 퍼센트',a))
  self.assertEqual(unmatched('공 점 공오육 퍼센트포인트',a),[])
 def test_admin_length_and_reading(self):
  d={'production_type':'창작·각색','sources':[],'material':{},'category':'story','script':['가'*25]*10}
  self.assertEqual(combine(check(d)),'pass');d['script']=['가'*24]*10
  self.assertEqual(combine(check(d)),'pass_with_flags');d['script']=['AI 즉 인공지능','1개']
  self.assertTrue(any('숫자·영문은 한글 읽기로' in x['reason'] and x['decision']=='revise' for x in check(d)))
 def test_site_grades(self):
  for grade,expected in [('자유 이용','pass'),('조건부(표시)','pass_with_flags'),('조건부(동일조건)','pass_with_flags'),('조건부(건별)','human_review'),('사실만 참고','pass_with_flags'),('허락 전 금지','reject')]:
   d={'production_type':'창작·각색','sources':[{'source_id':'SRC-1','site_grade':grade,'attribution':'출처'}]}
   self.assertEqual(combine(license_checks(d)),expected)
