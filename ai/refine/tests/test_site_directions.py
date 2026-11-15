import csv
import tempfile
import unittest
from pathlib import Path
from teum_refine.site import export_results
from teum_refine.rules import issue


class SiteDirections(unittest.TestCase):
    def test_specific_direction_and_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            items=[issue('R3','revise','불일치',i,'원문 수치로 바꾸기') for i in range(1,5)]
            export_results([dict(content_id='a',decision='revise',items=items)],tmp)
            path=Path(tmp)/'site_result.csv'
            self.assertTrue(path.read_bytes().startswith(b'\xef\xbb\xbf'))
            with path.open(encoding='utf-8-sig') as f:row=next(csv.DictReader(f))
            self.assertIn('1번 문장: 불일치 → 원문 수치로 바꾸기',row['사유'])
            self.assertNotIn('4번',row['사유'])

    def test_generic_omitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            export_results([dict(content_id='a',decision='revise',items=[issue('R3','revise','불일치')])],tmp)
            with (Path(tmp)/'site_result.csv').open(encoding='utf-8-sig') as f:row=next(csv.DictReader(f))
            self.assertEqual(row['사유'],'대본 전체: 불일치')
