import contextlib
import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from teum_refine.numerals import extract, normalize, unmatched, evidence_tokens
from teum_refine.rules import check, combine, copy_names, overlap_span, merge_r2, issue
from teum_refine.llm import review, r3_sources, R2_POLICY
from teum_refine.input import TYPES, load


def document(typ='창작·각색', script=None, source='원문'):
    return dict(content_id='audit', title='제목', production_type=typ,
                script=script or ['대본 문장'], material={}, generator={},
                sources=[dict(source_id='s1', _text=source, text=source,
                              attribution='원문', license=dict(kind='CC-BY', commercial=True, modify=True, share_alike=False))])


class Fake:
    prompt_style='xml-ko'
    def __init__(self, r2=None, entities=None, claims=None):
        self.calls=[]; self.r2=r2 or []; self.entities=entities or []; self.claims=claims or []
    def call(self, stage, payload, schema, instruction, system=None):
        self.calls.append((stage,payload,schema,instruction))
        if stage=='R2-llm':return {'items':self.r2}
        if stage=='R3-b':return {'claims':self.claims}
        if stage=='R3-d':return {'verdicts':[dict(claim_id=x['id'],verdict='supported',evidence='s1',note='근거 있음',relevant_conditions=[]) for x in self.claims if x['checkable']]}
        if stage=='R4':return dict(entities=self.entities,driving=[],harmful=[],injection=[])
        if stage=='R4c':return {'items':[]}
        if stage=='R5a':return {**{k:dict(score=5,reason='양호',sents=[]) for k in ('listening','structure','style','tts')},'hard_sents':[]}
        if stage.startswith('R5b'):return {**{f'q{i}':dict(answer=True,reason='양호',sents=[]) for i in range(1,6)},'overall_fun':3,'overall_reason':'보통'}
        raise AssertionError(stage)


class NumeralAudit(unittest.TestCase):
    def test_audited_sentences_roundtrip(self):
        cases=[
            ('이 백오십 분은 하루 삼십 분씩 주중에 나누어 실천할 수 있습니다.', [('150','분'),('30','분')]),
            ('덜 앉고 중간이나 고강도로 조금만 움직여도 일부 건강 이점이 생깁니다.', []),
            ('영국 상선이 발견한 이 배는 바로 메리 셀러스트호였습니다.', []),
            ('두 번째로 체력과 상관없이 산소 소비량을 재는 절대 강도가 있습니다.', []),
            ('만 열여덟 살부터 예순 살 성인은 얼마나 자야 할까요?', [('18','세'),('60','세')]),
            ('기준선과의 거리 차이 일 미터마다 일 점 팔 점을 가감합니다.', [('1','미터'),('1.8','점')]),
        ]
        for text,expected in cases:
            for form in (text,normalize(text)):
                with self.subTest(text=form):
                    self.assertEqual([(x['value'],x['unit']) for x in extract(form)],[(Decimal(v),u) for v,u in expected])
                    self.assertEqual(normalize(normalize(text)),normalize(text))
    def test_units_decimals_and_real_errors(self):
        for spoken,source in [('여섯 살 이상','6세 이상'),('삼십 센티미터','30cm인 나무'),('스무 번가량','20여 회'),('삼십 분','30 minutes'),('일 점 팔','1.8'),('일 점 팔 점','1.8점'),('일 점 팔 점','1.8'),('두 번','2번'),('이백오십 분','250분'),('이백 오십 분','250분'),('두 배가 되었다','2배'),('만 500원','10500원')]:
            with self.subTest(spoken=spoken):
                self.assertTrue(extract(spoken))
                self.assertEqual(unmatched(spoken,extract(source)),[])
        self.assertTrue(unmatched('일 점 구 점',extract('1.8점')))
        self.assertTrue(unmatched('일 점 팔 퍼센트',extract('1.8점')))
        self.assertTrue(unmatched('여섯 살',extract('7세')))
    def test_rule_pipeline_not_just_parser(self):
        d=document('사실해설',['이 백오십 분은 하루 삼십 분씩 나누어 실천합니다.','이점이 생깁니다.','두 번째로 소개합니다.','여섯 살입니다.','일 점 팔 점입니다.'],'150분 30분 6세 1.8점')
        self.assertFalse([x for x in check(d) if x['stage']=='R3-a'])
        d['script']=['백오십일 분을 실천합니다.']
        self.assertTrue([x for x in check(d) if x['stage']=='R3-a'])


class CopyAudit(unittest.TestCase):
    def test_threshold_and_no_containment_trigger(self):
        for length in (9,10,12,15):
            text='가나다라마바사아자차카타파하거너더러'[:length]
            d=document(script=[text],source=text)
            hits=[x for x in check(d) if x['stage']=='R2']
            self.assertEqual(bool(hits),length>=10)
            if hits:self.assertEqual(hits[0]['direction'],'이 구절을 바꿔 쓰기: '+text)
    def test_mask_names_titles_numbers_dates(self):
        d=document(script=['다음 날 아침 시청역 유실물 센터로 달려갔습니다.'],source='다음 날 아침 시청역 유실물 센터로 갔어요.')
        self.assertLess(overlap_span(d['script'][0],d['sources'][0]['_text'],copy_names(d,d['sources'][0]))[0],10)
        for text,names in [('천칠백십사년 오월 삼일',()),('12345678901234567890원',()),('《아주 길고 특별한 책의 제목》',()),('국제역사문화연구학회',()),('알렉산드르 세르게예비치 푸시킨',('알렉산드르 세르게예비치 푸시킨',))]:
            d=document(script=[text],source=text);d['material']['proper_nouns']=list(names)
            self.assertEqual(overlap_span(text,text,copy_names(d,d['sources'][0]))[0],0)
        # A shared mask must not count, nor join prose across a removed number.
        self.assertLess(overlap_span('가나다라마 1234567890 바사아자차','가나다라마 1234567890 바사아자차')[0],10)
    def test_valid_audit_prose_retained_and_span_is_real(self):
        sent='지하 주차장에 먼지만 쌓여 있던 아버지 승용차였습니다.'
        src='남은 선택지는 지하 주차장에 먼지만 쌓여 있던 아버지의 낡은 아반떼뿐이었다.'
        d=document(script=[sent],source=src)
        hits=[x for x in check(d) if x['stage']=='R2']
        self.assertEqual(len(hits),1)
        span=hits[0]['direction'].split(': ',1)[1]
        self.assertIn(span,sent); self.assertIn('먼지만 쌓여 있던',span)
    def test_r2_merge_keeps_required_fixes_separate_from_warnings(self):
        rows=[issue('R2','revise','표현 복사',1,'이 구절을 바꿔 쓰기: 원문'),issue('R2','pass_with_flags','부수 시간',1,'시간 바꾸기'),issue('R2','revise','금액',1,'금액 바꾸기'),issue('R2','revise','다른 문장',2)]
        merged=merge_r2(rows)
        self.assertEqual(len(merged),2); self.assertNotIn('시간 바꾸기',merged[0]['direction'])
        self.assertIn('금액 바꾸기',merged[0]['direction']);self.assertEqual(merge_r2(merged),merged)

    def test_cli_merges_rule_and_llm_items(self):
        from teum_refine.__main__ import main
        from types import SimpleNamespace
        d=document(script=['지하 주차장에 먼지만 쌓여 있던 아버지 승용차였습니다.'],source='지하 주차장에 먼지만 쌓여 있던 아버지의 낡은 차였다.')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);inp=p/'in.json';inp.write_text(json.dumps(d));out=p/'out'
            with patch('teum_refine.__main__.Client',return_value=SimpleNamespace(calls=[])),patch('teum_refine.__main__.review',return_value=([issue('R2','pass_with_flags','부수 시간',1,'시간 바꾸기')],{},[])),patch('sys.argv',['teum_refine','run',str(inp),'--out',str(out),'--skip-safety','--skip-dedup']),contextlib.redirect_stdout(io.StringIO()):main()
            result=json.loads((out/'audit.refine.json').read_text())
            hits=[x for x in result['items'] if x['stage']=='R2']
            self.assertEqual(len(hits),1);self.assertEqual(hits[0]['decision'],'revise')
            self.assertNotIn('시간 바꾸기',hits[0]['direction'])
            self.assertIn('참고 경고',hits[0]['reason'])


class LLMAudit(unittest.TestCase):
    def test_r2_type_gate_and_categories(self):
        rows=[dict(sent=i,category=k,reason=k,fix='수정 '+k) for i,k in enumerate(R2_POLICY,1)]
        for typ in TYPES:
            fake=Fake(r2=rows);items,raw,_=review(document(typ),fake)
            self.assertNotIn('processing_error',raw)
            self.assertEqual('R2-llm' in raw,typ=='창작·각색')
            actual={x['sent']:x['decision'] for x in items if x['stage']=='R2'}
            expected={x['sent']:R2_POLICY[x['category']] for x in rows if R2_POLICY[x['category']]} if typ=='창작·각색' else {}
            self.assertEqual(actual,expected)
    def test_r3_full_late_source_and_script_context(self):
        text='서론 '*10000+'전시장 칸막이 뒤에 감춰졌다.'
        d=document('사실해설',['전시 장소에 관한 설명입니다.','칸막이 뒤에 감춰졌습니다.'],text)
        fake=Fake(claims=[dict(id='c1',sent=2,text=d['script'][1],kind='event',checkable=True)])
        _,raw,_=review(d,fake);self.assertNotIn('processing_error',raw)
        _,payload,_,instruction=next(x for x in fake.calls if x[0]=='R3-d')
        self.assertEqual(payload['evidence']['sources'][0]['excerpt'],text)
        self.assertEqual([s['text'] for s in payload['script']],d['script'])
        self.assertIn('인접·연결 문장',instruction);self.assertIn('대사·간접화법',instruction)
    def test_r3_oversize_selects_late_passages_per_claim(self):
        text='무관한 내용입니다. '*500+'고유표지 감마 실험은 성공했다.'+'다른 내용입니다. '*500+'고유표지 델타 실험은 실패했다.'
        claims=[dict(id='c1',sent=1,text='감마 실험은 성공했다',checkable=True),dict(id='c2',sent=2,text='델타 실험은 실패했다',checkable=True)]
        d=document('사실해설',[c['text'] for c in claims],text)
        sources=r3_sources(d,claims,budget=2400)
        self.assertEqual(sources[0]['excerpt_mode'],'relevant_passages')
        self.assertIn('감마 실험은 성공했다',sources[0]['excerpt']);self.assertIn('델타 실험은 실패했다',sources[0]['excerpt'])
        self.assertLessEqual(len(sources[0]['excerpt']),2402)
    def test_r4_kind_filters_public_names_preserves_private_risk(self):
        kinds=['public_figure','historical_person','institution','fictional','private_person','private_contact']
        entities=[dict(sent=i,text=k,kind=k,identifiable=True,negative=False) for i,k in enumerate(kinds,1)]
        entities.append(dict(sent=7,text='실제 기업 비행 주장',kind='institution',identifiable=True,negative=True))
        items,raw,_=review(document('사실해설'),Fake(entities=entities))
        self.assertNotIn('processing_error',raw)
        self.assertEqual([(x['sent'],x['decision']) for x in items if x['stage']=='R4'],[(5,'revise'),(6,'revise'),(7,'human_review')])


class FeedbackRounds(unittest.TestCase):
    def test_cli_escalation_and_round(self):
        from teum_refine.__main__ import main
        for feedback,expected,round_no in [(None,'revise',1),(0,'revise',1),(1,'revise',2),(2,'human_review',3)]:
            with self.subTest(feedback=feedback),tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp);d=document(script=['숫자 123을 읽습니다.']);d['generator']={'revision':99}
                if feedback is not None:d['generator']['feedback_revision']=feedback
                inp=p/'in.json';inp.write_text(json.dumps(d));out=p/'out'
                with patch('sys.argv',['teum_refine','run',str(inp),'--out',str(out),'--skip-llm','--skip-safety','--skip-dedup']),contextlib.redirect_stdout(io.StringIO()):main()
                r=json.loads((out/'audit.refine.json').read_text())
                self.assertEqual((r['decision'],r['round'],r['pipeline_version']),(expected,round_no,'refine-v1.0.5'))
                self.assertEqual(json.loads((out/'revise.jsonl').read_text())['round'],round_no)
    def test_processing_error_keeps_feedback_round(self):
        from teum_refine.__main__ import main
        d=document();d['generator']={'revision':99,'feedback_revision':2}
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);inp=p/'in.json';inp.write_text(json.dumps(d));out=p/'out'
            with patch('teum_refine.__main__.check',side_effect=RuntimeError('fixture error')),patch('sys.argv',['teum_refine','run',str(inp),'--out',str(out),'--skip-llm','--skip-safety','--skip-dedup']),contextlib.redirect_stdout(io.StringIO()):main()
            result=json.loads((out/'audit.refine.json').read_text())
            self.assertTrue(result['processing_error']);self.assertEqual(result['round'],3)
    def test_invalid_count_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'in.json'
            for value in (True,-1,'2',2.5,None):
                d=document();d['generator']={'feedback_revision':value};p.write_text(json.dumps(d))
                with self.subTest(value=value),self.assertRaisesRegex(ValueError,'feedback_revision'):load(p)
        self.assertEqual(combine([issue('R2','revise','x')],feedback_revision=2),'human_review')
