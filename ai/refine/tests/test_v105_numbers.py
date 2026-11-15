import unittest
from test_v104 import Fake, document
import test_v105
from teum_refine.llm import adjudicate_numbers, review
from teum_refine.rules import check, dates_in
from teum_refine.numerals import extract


class CandidateJudge(Fake):
    def __init__(self, verdict='matches_source', fail=False):
        super().__init__()
        self.verdict=verdict
        self.fail=fail

    def call(self, stage, payload, schema, instruction, system=None):
        if stage=='R3-a-check':
            self.calls.append((stage,payload,schema,instruction))
            if self.fail:raise RuntimeError('fixture failure')
            return {'verdicts':[{**x,'verdict':self.verdict,'reason':'판정 근거'} for x in payload['candidates']]}
        return super().call(stage,payload,schema,instruction,system)


class Numbers105(unittest.TestCase):
    def test_calendar(self):
        for text in ('오랜 세월 수리해 보존했습니다','한 월','두 일','다섯 월'):
            self.assertEqual(dates_in(text),[])
            self.assertFalse([x for x in extract(text) if x['unit'] in ('월','일')])
        for text,expected in [('삼월',(None,3,None)),('시월',(None,10,None)),('유월',(None,6,None)),('3월',(None,3,None)),('이천이십육 년 삼월',(2026,3,None))]:
            self.assertEqual(dates_in(text)[0][0],expected)

    def test_candidate_verdicts(self):
        for typ in ('사실해설','창작·각색'):
            for sentence in ('두 개를 지닙니다.','삼월에 시작합니다.'):
                d=document(typ,script=[sentence],source='Comets have two tails.')
                items=check(d);original=[x for x in items if x['stage']=='R3-a']
                self.assertTrue(original)
                for verdict in ('matches_source','not_a_quantity','unsupported','contradicted'):
                    raw={};fake=CandidateJudge(verdict)
                    result=adjudicate_numbers(d,fake,items,raw)
                    self.assertEqual([x for x in result if x['stage']=='R3-a'],original if verdict in ('unsupported','contradicted') else [])
                    self.assertIn('sources',fake.calls[0][1]['evidence'])

    def test_failure_preserves(self):
        d=document(script=['두 개입니다.']);items=check(d)
        self.assertEqual(adjudicate_numbers(d,CandidateJudge(fail=True),items,{}),items)

    def test_skip_llm_preserves(self):
        r=test_v105.V105().run_cli(sentence='한 번의 점수가 나옵니다.')
        self.assertTrue(any(x['stage']=='R3-a' for x in r['items']))
        self.assertNotIn('R3-a-check',r['raw'])

    def test_no_candidates_no_call(self):
        fake=CandidateJudge()
        self.assertEqual(adjudicate_numbers(document(),fake,[],{}),[])
        self.assertEqual(fake.calls,[])

    def test_readings(self):
        sentence='백이십오 년쯤 자란 성체는 이 톤 이상 나가기도 합니다.'
        claim=dict(id='c1',sent=1,text=sentence,kind='number',checkable=True)
        fake=Fake(claims=[claim])
        _,raw,_=review(document('사실해설',[sentence],'It may weigh 2 or more tons'),fake)
        self.assertNotIn('processing_error',raw)
        for stage in ('R3-b','R3-d'):
            payload=next(x[1] for x in fake.calls if x[0]==stage)
            row=payload['script'][0] if stage=='R3-b' else payload['claims'][0]
            self.assertIn(dict(value='2',unit='톤'),row['numeric_readings'])
            self.assertIn(dict(value='125',unit='년'),row['numeric_readings'])
