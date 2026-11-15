import json,tempfile,unittest
from pathlib import Path
from decimal import Decimal
from teum_refine.input import load,output_metadata
from teum_refine.rules import license_checks,check
from teum_refine.numerals import extract,evidence_tokens,unmatched
class ShipReview(unittest.TestCase):
 def test_license_boolean_and_null(self):
  for key in ('commercial','modify'):
   for value in (False,'no','false'):
    lic=dict(kind='CC-BY',commercial=True,modify=True,share_alike=False);lic[key]=value
    d=dict(production_type='사실해설',sources=[dict(source_id='s',license=lic,attribution='x')])
    with self.subTest(key=key,value=value):self.assertTrue(any(x['decision']=='reject' for x in license_checks(d)))
  for key in ('kind','commercial','modify','share_alike'):
   lic=dict(kind='CC-BY',commercial=True,modify=True,share_alike=False);lic[key]=None
   with self.subTest(null=key):self.assertTrue(any(x['decision']=='human_review' and '권리 미확인' in x['reason'] for x in license_checks(dict(production_type='사실해설',sources=[dict(source_id='s',license=lic)]))))
 def test_harm_and_invest_never_reject(self):
  from teum_refine.legacy_rules import HARM_WORDS,ADVICE_WORDS
  d=dict(production_type='사실해설',category='economy',material={},sources=[],script=[next(iter(HARM_WORDS)),next(iter(ADVICE_WORDS))])
  items=check(d);self.assertTrue(any(x['stage']=='R4' and x['decision']=='human_review' for x in items));self.assertTrue(any(x['stage']=='R4c' and x.get('category')=='invest' and x['decision']=='human_review' for x in items));self.assertFalse(any(x['decision']=='reject' for x in items))
 def test_common_words_are_not_numbers(self):
  for word in ['이자는','이사','일상','사이','구조','오늘','삼촌','십분','백화점','천천히','만나다','육아','칠판']:
   with self.subTest(word=word):self.assertEqual(extract(word),[])
  self.assertEqual(extract('삼십'),[])
  self.assertEqual(extract('앞두 명'),[])
  for text,value in [('두 명',2),('(십 분)',10),('삼 점 육일삼 퍼센트','3.613'),('일억 원',100000000)]:
   with self.subTest(text=text):self.assertEqual(extract(text)[0]['value'],Decimal(str(value)))
 def test_fact_units_survive(self):
  facts=[dict(text='6.02%',quote='5.03%',label='0.056%포인트'),dict(value=3.613,prev=3.669,change=-0.056,unit='%'),dict(change_pct=-6.02),dict(change_pp=-0.056),{'%포인트':0.056}]
  d=dict(material={'facts':facts},sources=[]);a=evidence_tokens(d)
  self.assertEqual(unmatched('육 점 공이 퍼센트, 오 점 공삼 퍼센트, 공 점 공오육 퍼센트포인트',a),[])
  self.assertEqual(unmatched('3.613% 3.669% 0.056',a),[])
  self.assertTrue(unmatched('0.056%',a));self.assertTrue(unmatched('6.02%포인트',a))
  for fact,text in [(dict(change_pct=-6.02),'6.02%'),(dict(change_pp=-0.056),'0.056%포인트'),(dict(value=3.613,prev=3.669,change=-0.056,unit='%'),'3.613% 3.669% 0.056%포인트')]:
   with self.subTest(fact=fact):self.assertEqual(unmatched(text,evidence_tokens(dict(material={'facts':[fact]},sources=[]))),[])
  sample=load(Path(__file__).parents[1]/'examples/samples/economy.json')
  self.assertEqual(unmatched('6.02% 5.03% 0.056%포인트',evidence_tokens(sample)),[])
 def test_type_variants(self):
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder)/'case.json'
   for typ,expected in [('창작각색','창작·각색'),('창작각색형','창작·각색'),(' 창작 ㆍ 각색 형 ','창작·각색'),('창작・각색형','창작·각색'),('해석감상','해석·감상'),('해석 · 감상 형','해석·감상')]:
    p.write_text(json.dumps(dict(content_id='x',title='x',production_type=typ,script=['본문'],sources=[])))
    with self.subTest(typ=typ):d=load(p);self.assertEqual(d['production_type'],expected);self.assertEqual(output_metadata(d)['production_type_original'],typ)
 def test_open_metadata_passthrough(self):
  d=dict(content_id='x',title='x',production_type='사실해설',script=['본문'],sources=[],material={},format='정보',target_sec=60,tags=['태그'],tts_checked=False,future_metadata={'nested':[None,True]})
  meta=output_metadata(d)
  for key in ('format','target_sec','tags','tts_checked','future_metadata'):self.assertEqual(meta[key],d[key])
  for key in ('script','sources','material'):self.assertNotIn(key,meta)
