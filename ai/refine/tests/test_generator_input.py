import json,tempfile,unittest
from pathlib import Path
from teum_refine.input import load,output_metadata,checkable_facts,is_economy
from teum_refine.rules import check,license_checks
from teum_refine.__main__ import write_batch_reports
class GeneratorInput(unittest.TestCase):
 def test_real_generator_contract(self):
  path=Path(__file__).parent/'fixtures/generator/real_readaloud.json';original=json.loads(path.read_text());d=load(path)
  self.assertEqual(d['production_type'],'원문낭독');self.assertEqual(d['sources'][0]['_text'],original['sources'][0]['text']);self.assertEqual(checkable_facts(d),[])
  self.assertEqual(output_metadata(d)['production_type_original'],'원문낭독형');self.assertEqual(output_metadata(d)['generator'],original['generator'])
  self.assertTrue(any(x['reason']=='테스트용 원천' for x in license_checks(d)))
  self.assertFalse(any(x['stage']=='R2' for x in check(d)))
  d['material']['conflict']='이야기의 내면 갈등';self.assertFalse(any(x['reason']=='소재 사실 충돌' for x in check(d)))
  d['script'][1]='원문을 바꾼 문장입니다.';issues=check(d);self.assertTrue(any(x['stage']=='R2' and x['sent']==2 for x in issues))
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);write_batch_reports([dict(content_id='x',round=1,decision='revise',fun_score=0,items=issues)],root)
   item=json.loads((root/'revise.jsonl').read_text())['items'][0];self.assertEqual(item['sentence'],item['sent']);self.assertEqual(item['direction'],item['fix'])
   data={k:original[k] for k in ('content_id','title','production_type','script','sources')};data['production_type']='창작·각색형';data['sources'][0]['license']={k:None for k in ('kind','commercial','modify','share_alike')};p=root/'input.json';p.write_text(json.dumps(data));minimal=load(p)
   self.assertEqual(minimal['production_type'],'창작·각색');self.assertEqual(minimal['category'],'unknown');self.assertTrue(any(x['decision']=='human_review' and '권리 미확인' in x['reason'] for x in license_checks(minimal)))
   self.assertTrue(is_economy({'category':'C08'}));self.assertFalse(is_economy({'category':'C15'}))
