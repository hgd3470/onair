import unittest
from teum_refine.rules import combine,license_checks,numbers_in,grams,lcs_len
class Rules(unittest.TestCase):
 def test_priority(self):
  self.assertEqual(combine([{'decision':'revise'},{'decision':'reject'}],2),'reject')
  self.assertEqual(combine([{'decision':'revise'}],2),'human_review')
  self.assertEqual(combine([]),'pass')
 def test_numbers(self): self.assertEqual(numbers_in('5천만 원과 250만 원'),[50000000,2500000])
 def test_copy(self):
  self.assertEqual(lcs_len('가나다 라마','바 가나다라마 사'),5)
  self.assertEqual(grams('가나다라마'),{'가나다라','나다라마'})
 def test_license(self):
  d={'production_type':'사실해설','sources':[{'source_id':'x','attribution':'a','license':{'kind':'ecos-other-org','commercial':'non-commercial-only','modify':'values-unchanged'}}]}
  self.assertEqual(combine(license_checks(d)),'pass_with_flags')
  d['sources'][0]['license']['commercial']='no'
  self.assertEqual(combine(license_checks(d)),'reject')
 def test_no_source_creation_and_unknown_external(self):
  d={'production_type':'창작·각색','sources':[]}
  self.assertEqual(combine(license_checks(d)),'pass')
  d['sources']=[{'source_id':'x','attribution':'x','license':{'kind':'unknown','commercial':'unknown','modify':'unknown'}}]
  self.assertEqual(combine(license_checks(d)),'human_review')
 def test_length_rate(self):
  from teum_refine.rules import check
  d={'production_type':'창작·각색','sources':[],'material':{},'category':'story','script':['가'*30]*10,'target_sec':60}
  self.assertEqual(combine(check(d)),'pass')
  self.assertEqual(combine(check(d,6.5)),'pass')

 def test_dates(self):
  from teum_refine.rules import strip_dates
  self.assertEqual(numbers_in(strip_dates('2026년 11월 7일 지난 거래일인 11월 6일 3년 250만 원')),[2500000])
 def test_kanana_review(self):
  from teum_refine.rules import kanana_items
  self.assertEqual(combine(kanana_items('<UNSAFE-S2>')),'human_review')
  self.assertEqual(combine(kanana_items('<UNSAFE-S2>')+[{'decision':'reject'}]),'reject')
 def test_license_mapping(self):
  from teum_refine.input import mapped_license
  self.assertEqual(mapped_license('WEB','https://ebible.org/eng-web/LUK15.htm','Public Domain')['kind'],'PD')
  self.assertEqual(mapped_license('WEB','https://ebible.org/eng-web/LUK15.htm','')['kind'],'unknown')

 def test_listening_rules(self):
  from teum_refine.rules import listening_checks
  self.assertEqual(listening_checks(['짧은 문장입니다.']),[])
  for text in ['괄호(설명)','AI 서비스','12345원','3.14입니다','1과 2와 3','5% 증가','가'*44]:
   self.assertEqual(listening_checks([text])[0]['decision'],'revise')
  self.assertEqual(listening_checks(['AI 즉 인공지능 기술입니다.'])[0]['decision'],'revise')
  self.assertEqual(listening_checks(['AI 기술입니다.','이는 인공지능을 뜻합니다.'])[0]['decision'],'revise')
 def test_dates_compare(self):
  from teum_refine.rules import date_checks
  d={'material':{'as_of':'2026-11-06','facts':[]},'sources':[],'script':['11월 7일 브리핑입니다.','지난 거래일인 11월 6일입니다.']}
  self.assertEqual(date_checks(d),[])
  d['script'][1]='지난 거래일인 11월 9일입니다.'
  self.assertIn('이후',date_checks(d)[0]['reason'])
  d['script'][1]='11월 5일입니다.'
  self.assertEqual(date_checks(d)[0]['stage'],'R3-a')
