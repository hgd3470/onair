import unittest
from teum_refine.rules import listening_checks,date_checks
class Calibration(unittest.TestCase):
 def test_length_boundary(self):
  self.assertEqual(listening_checks(['가'*43]),[])
  self.assertEqual(listening_checks(['가'*44])[0]['sent'],1)
 def test_known_dates_in_labels_and_metadata(self):
  d={'script':['2026년 11월 7일 브리핑입니다.','11월 6일과 11월 5일 자료입니다.'],'material':{'facts':[{'id':'f','label':'2026년 11월 5일 통계'}]},'sources':[{'info_as_of':'2026-11-06'}]}
  self.assertEqual(date_checks(d),[])
