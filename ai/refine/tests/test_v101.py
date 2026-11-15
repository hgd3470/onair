import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from teum_refine.numerals import evidence_tokens,unmatched
from teum_refine.rules import issue
from teum_refine.llm import review,Refusal
from teum_refine.budget import reserve,spent
from teum_refine.dedup import Archive,duplicate_item
class Changes101(unittest.TestCase):
 def test_units(self):
  for source,script in [('8개월째','여덟 달째'),('2년','두 해'),('2시간','두 시'),('3분','삼 분'),('5%','오 퍼센트'),('2500000원','이백오십만 원화'),('100000000원화','일억 원')]:
   with self.subTest(script=script):self.assertEqual(unmatched(script,evidence_tokens({'material':{'facts':[{'text':source}]},'sources':[]})),[])
  self.assertTrue(unmatched('오 퍼센트포인트',evidence_tokens({'material':{'facts':[{'text':'5%'}]},'sources':[]})))
 def test_fixes(self):
  for stage,reason,expected in [('R5a','숫자·영문은 한글 읽기로','한글 읽기로 바꾸기'),('R5a','한글 50음절 > 43','두 문장'),('R5a','괄호/대괄호','괄호 없이'),('R2','원문 중복','다시 쓰기'),('R2','원천 고유 요소 잔존: 강남','강남 표현을 일반화'),('R1','권리 미확인','이용 조건'),('R5a','본문 200자: 권장 250~450자 밖','250~450자로')]:
   self.assertIn(expected,issue(stage,'revise',reason)['fix'])
 def test_output_root_budget(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'REFINE_TOTAL_BUDGET_USD':'1'}):
   root=Path(tmp)
   for name in ('a','b'):
    p=root/'cases'/name/'cache';p.mkdir(parents=True);(p/'cost.json').write_text(json.dumps({'audit':{'cost_usd':.4}}))
   self.assertAlmostEqual(spent(root),.8)
   with self.assertRaisesRegex(RuntimeError,'budget'): 
    with reserve(root,.21):pass
   with reserve(root,.1):
    with self.assertRaises(RuntimeError):
     with reserve(root,.11):pass
 def test_partial_llm_result(self):
  for failure in (Refusal('policy'),TimeoutError('timeout')):
   class Fake:
    prompt_style='xml-ko'
    def call(self,stage,*args,**kwargs):
     if stage=='R4':return dict(entities=[],driving=[dict(sent=1,reason='운전 중 검색',fix='정차 후 검색으로 바꾸기')],harmful=[],injection=[])
     raise failure
   items,raw,fun=review(dict(production_type='사실해설',script=['검색하세요']),Fake(),comparison=True)
   self.assertEqual(items[0]['fix'],'정차 후 검색으로 바꾸기');self.assertEqual(items[-1]['stage'],'R4c');self.assertEqual(items[-1]['decision'],'human_review');self.assertIn('R4',raw)
 def test_duplicate_id_batch(self):
  from teum_refine.__main__ import main
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);inputs=root/'in';inputs.mkdir();out=root/'out'
   d=dict(content_id='shared',title='제목',production_type='창작각색',script=['짧은 문장'],sources=[])
   for n in ('1','2'):(inputs/(n+'.json')).write_text(json.dumps(d))
   with patch('sys.argv',['teum_refine','run',str(inputs),'--out',str(out),'--skip-llm','--skip-safety','--skip-dedup']):main()
   first=json.loads((out/'shared.refine.json').read_text());second=json.loads((out/'shared__2.refine.json').read_text())
   self.assertEqual(first['decision'],'pass_with_flags');self.assertEqual(second['decision'],'human_review');self.assertEqual(second['items'][0]['problem'],'중복 관리번호')
 def test_site_duplicate_reaches_batch_review(self):
  import csv
  from teum_refine.site import convert_kit
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);kit=root/'kit';kit.mkdir()
   (kit/'원천목록.csv').write_text('id,name,grade,credit,url\n')
   with (kit/'검수대상.csv').open('w') as f:
    w=csv.DictWriter(f,fieldnames=['id','title','format','body','source_note']);w.writeheader()
    for _ in range(2):w.writerow(dict(id='same',title='제목',format='창작',body='본문',source_note=''))
   paths=list(convert_kit(kit,root/'inputs').glob('*.json'));self.assertEqual(len(paths),2);self.assertEqual([json.loads(p.read_text())['content_id'] for p in paths],['same','same'])
 def test_archive(self):
  class FakeEncoder:
   def encode(self,ds):return [[1.,0.] for d in ds]
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'archive.jsonl';a=Archive(p,FakeEncoder(),.8,.95)
   d=dict(content_id='a',title='제목',script=['본문'],version=1)
   items,row=a.consider(d);self.assertFalse(items);a.accept(row,'pass');a.commit()
   repeated=Archive(p,FakeEncoder(),.8,.95);self.assertFalse(repeated.consider(d)[0])
   b=Archive(p,FakeEncoder(),.8,.95);items,row=b.consider({**d,'version':2});self.assertFalse(items);b.accept(row,'pass_with_flags')
   items,row=b.consider({**d,'content_id':'b'});self.assertEqual(items[0]['decision'],'human_review');b.accept(row,'human_review');b.commit();b.commit()
   rows=[json.loads(l) for l in p.read_text().splitlines()];self.assertEqual(len(rows),2);self.assertEqual([r['version'] for r in rows],[1,2])
   c=Archive(Path(tmp)/'empty.jsonl',FakeEncoder(),.8,.95);_,row=c.consider(d);c.accept(row,'pass');items,_=c.consider({**d,'content_id':'next'});self.assertEqual(items[0]['stage'],'R6')
 def test_r6_cli_archive_integration(self):
  from teum_refine.__main__ import main
  class FakeEncoder:
   def encode(self,ds):return [[1.,0.] for _ in ds]
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);inputs=root/'in';inputs.mkdir();out=root/'out'
   for cid in ('a','b'):(inputs/(cid+'.json')).write_text(json.dumps(dict(content_id=cid,title='제목',production_type='창작각색',script=['짧은 본문'],sources=[])))
   with patch('teum_refine.dedup.Encoder',FakeEncoder),patch('sys.argv',['teum_refine','run',str(inputs),'--out',str(out),'--skip-llm','--skip-safety']):main()
   self.assertEqual(json.loads((out/'b.refine.json').read_text())['decision'],'human_review')
   self.assertEqual(len((out/'dedup-index.jsonl').read_text().splitlines()),1)
 def test_r6_same_id_reruns_have_no_item(self):
  from teum_refine.__main__ import main
  class FakeEncoder:
   def encode(self,ds):return [[1.,0.] for _ in ds]
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);source=root/'script.json';index=root/'index.jsonl'
   d=dict(content_id='a',title='제목',production_type='창작각색',script=['본문'],sources=[],version=1)
   source.write_text(json.dumps(d));archive=Archive(index,FakeEncoder());_,row=archive.consider(d);archive.accept(row,'pass');archive.commit()
   for run in range(2):
    out=root/str(run)
    with patch('teum_refine.dedup.Encoder',FakeEncoder),patch('sys.argv',['teum_refine','run',str(source),'--out',str(out),'--dedup-index',str(index),'--skip-llm','--skip-safety']):main()
    result=json.loads((out/'a.refine.json').read_text());self.assertFalse([i for i in result['items'] if i['stage']=='R6'])
   self.assertEqual(len(index.read_text().splitlines()),1)
 def test_r6_only_final_passed_scripts_are_batch_candidates(self):
  from teum_refine.__main__ import main
  class FakeEncoder:
   def encode(self,ds):return [[1.,0.] for _ in ds]
  for decision in ('reject','revise','human_review'):
   with self.subTest(decision=decision),tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);inputs=root/'inputs';inputs.mkdir();out=root/'out'
    for cid in ('a','b'):(inputs/(cid+'.json')).write_text(json.dumps(dict(content_id=cid,title='같은 제목',production_type='창작각색',script=['같은 본문'],sources=[])))
    with patch('teum_refine.dedup.Encoder',FakeEncoder),patch('teum_refine.__main__.check',side_effect=[[issue('R5a',decision,'선행 문제',1)],[]]),patch('sys.argv',['teum_refine','run',str(inputs),'--out',str(out),'--workers','1','--skip-llm','--skip-safety']):main()
    first=json.loads((out/'a.refine.json').read_text());second=json.loads((out/'b.refine.json').read_text());self.assertEqual(first['decision'],decision);self.assertEqual(second['decision'],'pass');self.assertFalse([i for i in second['items'] if i['stage']=='R6'])
    rows=[json.loads(l) for l in (out/'dedup-index.jsonl').read_text().splitlines()];self.assertEqual([r['content_id'] for r in rows],['b'])
 def test_r6_decisions(self):
  self.assertEqual(duplicate_item('x',.79,.8,.95),[])
  self.assertEqual(duplicate_item('x',.8,.8,.95)[0]['decision'],'pass_with_flags')
  self.assertEqual(duplicate_item('x',.95,.8,.95)[0]['decision'],'human_review')
