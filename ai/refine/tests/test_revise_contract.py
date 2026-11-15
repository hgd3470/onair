import csv,json,tempfile,unittest
from pathlib import Path
from teum_refine.rules import issue,normalize_item
from teum_refine.__main__ import write_batch_reports
from teum_refine.site import export_results
class ReviseContract(unittest.TestCase):
 def test_fixable_and_concrete_aliases(self):
  for stage in ('R0','R1','R4-a','R4c'):
   x=issue(stage,'human_review','확인 필요');self.assertFalse(x['fixable']);self.assertTrue(x['fix']);self.assertEqual(x['fix'],x['direction'])
  self.assertTrue(issue('R5a','revise','숫자·영문은 한글 읽기로',2)['fixable'])
  self.assertFalse(issue('R5a','human_review','TimeoutError',fixable=False)['fixable'])
 def test_rewrite_and_blocking_split(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);rights=issue('R1','human_review','권리 미확인');rewrite=issue('R5a','revise','숫자·영문은 한글 읽기로',2)
   results=[dict(content_id='mixed',round=1,decision='human_review',fun_score=0,items=[rights,rewrite]),dict(content_id='blocked',round=1,decision='human_review',fun_score=0,items=[rights,issue('R4c','human_review','invest',3)])]
   write_batch_reports(results,root);rows=[json.loads(l) for l in (root/'revise.jsonl').read_text().splitlines()]
   self.assertEqual(len(rows),1);r=rows[0];self.assertEqual(r['script_id'],'mixed');self.assertEqual(len(r['items']),1);self.assertTrue(r['items'][0]['fixable']);self.assertEqual(r['items'][0]['sentence'],2);self.assertEqual(len(r['blocking']),1);self.assertFalse(r['blocking'][0]['fixable']);self.assertIsNone(r['blocking'][0]['sentence'])
 def test_script_scope_and_site_labels(self):
  whole=issue('R5a','pass_with_flags','본문 200자: 권장 250~450자 밖',0)
  self.assertIsNone(whole['sent']);self.assertIsNone(whole['sentence']);self.assertEqual(whole['scope'],'script')
  local=issue('R5a','revise','괄호',3);self.assertEqual(local['scope'],'sentence');self.assertEqual(local['sentence'],3)
  self.assertIsNone(normalize_item({'stage':'R1','sent':0,'reason':'권리','decision':'human_review'})['sent'])
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);export_results([dict(content_id='whole',decision='pass_with_flags',items=[whole]),dict(content_id='local',decision='revise',items=[local]),dict(content_id='rights',decision='human_review',items=[issue('R1','human_review','권리 미확인')])],root)
   rows=list(csv.DictReader((root/'site_result.csv').open(encoding='utf-8-sig')))
   self.assertTrue(rows[0]['사유'].startswith('대본 전체:'));self.assertTrue(rows[1]['사유'].startswith('3번 문장:'));self.assertIn('대본 전체:',rows[2]['사유']);self.assertNotIn('0번 문장',(root/'site_result.csv').read_text(encoding='utf-8-sig'))

 def test_only_must_fix_items_are_rewrite_work(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);must=issue('R5a','revise','괄호',2);minor=issue('R5a','pass_with_flags','minor: 약간 복잡',3);length=issue('R5a','pass_with_flags','본문 200자: 권장 250~450자 밖');attribution=issue('R1','pass_with_flags','출처 표시');review=issue('R4','human_review','실체 식별',4)
   result=dict(content_id='mixed',round=1,decision='human_review',fun_score=None,items=[must,minor,length,attribution,review])
   write_batch_reports([result],root);r=json.loads((root/'revise.jsonl').read_text())
   self.assertEqual([x['reason'] for x in r['items']],[must['reason']]);self.assertEqual([x['reason'] for x in r['warnings']],[minor['reason'],length['reason'],attribution['reason']]);self.assertEqual([x['reason'] for x in r['blocking']],[attribution['reason']]);self.assertEqual(len(result['items']),5)
 def test_warnings_only_produce_no_rewrite_line(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);warnings=[issue('R5a','pass_with_flags','minor: 약간 복잡',1),issue('R6','pass_with_flags','중복 의심'),issue('R3','pass_with_flags','not_found')]
   results=[dict(content_id='warnings',round=1,decision='pass_with_flags',fun_score=None,items=warnings),dict(content_id='blocked_warnings',round=1,decision='human_review',fun_score=None,items=warnings+[issue('R1','human_review','권리 미확인')])]
   write_batch_reports(results,root);self.assertEqual((root/'revise.jsonl').read_text(),'')
