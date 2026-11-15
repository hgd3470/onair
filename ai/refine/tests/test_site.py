import csv,tempfile,unittest
from pathlib import Path
from teum_refine.site import convert_kit,export_results,note_sources
from teum_refine.input import load
class Site(unittest.TestCase):
 def test_two_script_fixture(self):
  sources,_=note_sources('출처: https://example.org/article\n이용조건: 미확인\n작성 범위: 사실 참고',{})
  self.assertEqual(sources[0]['license']['kind'],'unknown')
  self.assertEqual(sources[0]['source_url'],'https://example.org/article')
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp);paths=list(convert_kit(Path(__file__).parent/'fixtures/site',out/'inputs').glob('*.json'));self.assertEqual(len(paths),2);ids=[load(p)['content_id'] for p in paths]
   export_results([dict(content_id=ids[0],decision='pass_with_flags',items=[dict(decision='pass_with_flags',reason='출처 표시')]),dict(content_id=ids[1],decision='human_review',items=[dict(decision='human_review',reason='허락 확인')])],out)
   self.assertTrue((out/'site_result.csv').read_bytes().startswith(b'\xef\xbb\xbf'))
   rows=list(csv.DictReader((out/'site_result.csv').read_text(encoding='utf-8-sig').splitlines()));self.assertEqual(rows[0]['판정'],'통과');self.assertEqual(rows[1]['판정'],'');self.assertTrue(rows[1]['사유'].startswith('사람 확인 필요:'))
