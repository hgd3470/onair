import unittest
from teum_refine.rules import license_checks, issue
from teum_refine.llm import adjudicate_overlaps, reference_date


def news(**src):
    s = {'source_id': 'IC-1', 'license': {'kind': 'unknown'}, 'attribution': '이데일리',
         'source_kind': 'user_summary', 'original_sources': [{'author': '이데일리'}], 'info_as_of': '2026-11-12', **src}
    return {'production_type': '사실해설', 'sources': [s], 'script': ['국토교통부와 한국토지주택공사가 수수료를 낮춥니다.', '수요 진작 정책의 필요성은 크지 않다고 밝혔습니다.']}


class NewsHandoff(unittest.TestCase):
    def test_article_license_unknown_is_flag_not_review(self):
        out = license_checks(news())
        self.assertEqual([x['decision'] for x in out], ['pass_with_flags'])

    def test_other_unknown_sources_still_go_to_review(self):
        d = news(source_kind=None)
        self.assertIn('human_review', [x['decision'] for x in license_checks(d)])

    def test_reference_date_prefers_source_date(self):
        self.assertEqual(reference_date({**news(), 'created_at': '2026-11-13T10:00:00+09:00'}), '2026-11-12')

    def test_overlap_adjudication(self):
        items = [issue('R2', 'revise', '원문 중복 lcs=14', 1, '이 구절을 바꿔 쓰기: 국토교통부와 한국토지주택공사'),
                 issue('R2', 'revise', '원문 중복 lcs=18', 2, '이 구절을 바꿔 쓰기: 수요 진작 정책의 필요성은 크지 않다고 밝혔')]
        class Fake:
            def call(self, stage, payload, schema, instruction, system=None):
                return {'verdicts': [{'sent': 1, 'span': '국토교통부와 한국토지주택공사', 'verdict': 'official_name', 'reason': ''},
                                     {'sent': 2, 'span': '수요 진작 정책의 필요성은 크지 않다고 밝혔', 'verdict': 'copied_expression', 'reason': ''}]}
        d = news()
        d['sources'][0]['_text'] = '국토교통부와 한국토지주택공사가 밝혔다. 수요 진작 정책의 필요성은 크지 않다고 밝혔다.'
        out = adjudicate_overlaps(d, Fake(), items, {})
        self.assertEqual([(x['sent'], x['decision']) for x in out], [(2, 'revise')])
        story = {**d, 'production_type': '창작·각색'}
        self.assertEqual(adjudicate_overlaps(story, Fake(), items, {}), items)

    def test_overlap_merged_with_warning_and_spacing(self):
        d = news()
        d['sources'][0]['_text'] = '국토교통부와  한국토지주택공사는 발표했다.'
        item = issue('R2', 'revise', '원문 중복 lcs=14; 참고 경고: 원문 중복 lcs=11', 1,
                     '이 구절을 바꿔 쓰기: 국토교통부와 한국토지주택공사')
        class Fake:
            def call(self, stage, payload, schema, instruction, system=None):
                assert payload['candidates'][0]['source_context']
                return {'verdicts': [{'sent': 1, 'span': '국토교통부와 한국토지주택공사', 'verdict': 'official_name', 'reason': ''}]}
        self.assertEqual(adjudicate_overlaps(d, Fake(), [item], {}), [])

    def test_overlap_without_source_passage_keeps_revise(self):
        d = news()
        d['sources'][0]['_text'] = '전혀 다른 원문'
        item = issue('R2', 'revise', '원문 중복 lcs=14', 1, '이 구절을 바꿔 쓰기: 국토교통부와 한국토지주택공사')
        class Fake:
            def call(self, stage, payload, schema, instruction, system=None):
                return {'verdicts': [{'sent': 1, 'span': '국토교통부와 한국토지주택공사', 'verdict': 'official_name', 'reason': ''}]}
        self.assertEqual(adjudicate_overlaps(d, Fake(), [item], {}), [item])


if __name__ == '__main__':
    unittest.main()
