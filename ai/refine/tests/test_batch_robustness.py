import csv,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from teum_refine.__main__ import main
from teum_refine.evaluation import evaluate
class BatchRobustness(unittest.TestCase):
 def test_malformed_script_does_not_stop_run_or_eval(self):
  class FakeClient:
   def __init__(self,*args,**kwargs):self.calls=[]
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);inputs=root/'inputs';inputs.mkdir()
   (inputs/'00-bad.json').write_text('{broken')
   original=Path(__file__).parent/'fixtures/eval/cases/original.json'
   (inputs/'99-good.json').write_bytes(original.read_bytes())
   truth=root/'truth.jsonl';truth.write_text('\n'.join(json.dumps(dict(case_id=cid,base_id=None,flaw='none',sent=None,expected_stage=None,expected_decision='pass',note='fixture')) for cid in ('00-bad','99-good')))
   for command in ('run','eval'):
    with self.subTest(command=command):
     out=root/command
     if command=='run':
      with patch('sys.argv',['teum_refine','run',str(inputs),'--out',str(out),'--skip-llm','--skip-safety','--skip-dedup']),patch('teum_refine.__main__.Client',side_effect=AssertionError('No API')):main()
     else:
      with patch('teum_refine.evaluation.Client',FakeClient),patch('teum_refine.evaluation.review',return_value=([],{},[])),patch('teum_refine.__main__.safety',return_value=([],'<SAFE>')):evaluate(inputs,truth,out)
     rows=list(csv.DictReader((out/'summary.csv').read_text().splitlines()));self.assertEqual(len(rows),2)
     bad=json.loads((out/'00-bad.refine.json').read_text());self.assertEqual(bad['decision'],'human_review');error=next(x for x in bad['items'] if x['stage']=='R0');self.assertEqual(error['problem'],'처리 오류');self.assertTrue(error['reason'].startswith('JSONDecodeError:'))
     self.assertTrue(any(r['decision']!='human_review' for r in rows));self.assertNotIn('처리 오류',(out/'revise.jsonl').read_text());self.assertEqual(json.loads((out/'cost.json').read_text())['cost_usd'],0)
