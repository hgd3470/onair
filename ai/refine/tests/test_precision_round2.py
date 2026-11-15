"""Invented fixtures only; no audited text is added to prompts or rules."""
import copy
import unittest
from test_v104 import Fake, document
from teum_refine.llm import review, claim_context, r3_evidence, r3_fix, VERDICTS, QUALITY, R3_VERDICT_INSTRUCTION, R4_INSTRUCTION, R5A_INSTRUCTION

class Responses(Fake):
    def __init__(self, *, verdict='supported', severity='major', hard=None, scores=None, **kwargs):
        super().__init__(**kwargs)
        self.verdict=verdict;self.severity=severity;self.hard=hard or [];self.scores=scores or {}
    def call(self, stage, payload, schema, instruction, system=None):
        result=super().call(stage,payload,schema,instruction,system)
        if stage=='R3-d':
            for row in result['verdicts']:
                row.update(verdict=self.verdict,severity=self.severity,unsupported_detail='',note='주장과 원문 대조',evidence='s1: 자료를 확인했다')
        if stage=='R5a':
            result['hard_sents']=self.hard
            for key,score in self.scores.items():result[key].update(score=score,reason='전체 구성이나 표현의 문제',sents=[1])
        return result

def claim(n=1):return dict(id='c1',sent=n,text='도구는 녹색이었다.',kind='event',checkable=True)

class Grounding(unittest.TestCase):
    def test_card_annotations_are_lookup_hints_not_evidence(self):
        d=document('사실해설',source='도구는 녹색이었다.')
        d['material']['facts']=[dict(id='f1',quote='도구는 녹색이었다.',text='녹색 도구',conditions=['창고에서만 사용']),dict(id='f2',text='검증되지 않은 메모'), '추출기 주석']
        before=copy.deepcopy(d);evidence=r3_evidence(d,[claim()])
        self.assertEqual(evidence['facts'],[dict(id='f1',quote='도구는 녹색이었다.')])
        self.assertEqual(evidence['lookup_hints'][0]['conditions'],['창고에서만 사용'])
        self.assertTrue(all('quote' not in h for h in evidence['lookup_hints'] if isinstance(h,dict)))
        self.assertEqual(d,before)
    def test_hint_only_input_does_not_count_as_source(self):
        d=document('사실해설',source='');d['material']['facts']=[dict(id='f1',text='추출한 요약',conditions=['검증 안 된 제한'])]
        items,raw,_=review(d,Responses(verdict='overstated',claims=[claim()]))
        self.assertNotIn('processing_error',raw)
        self.assertEqual([x['decision'] for x in items if x['stage']=='R3'],['pass_with_flags'])
    def test_source_metadata_requires_field_and_value(self):
        class Metadata(Responses):
            def call(self,stage,payload,schema,instruction,system=None):
                result=super().call(stage,payload,schema,instruction,system)
                if stage=='R3-d':result['verdicts'][0]['evidence']='s1 provider: 예시 기록실'
                return result
        d=document('사실해설',source='');d['sources'][0]['provider']='예시 기록실'
        items,raw,_=review(d,Metadata(verdict='contradicted',severity='minor',claims=[claim()]))
        self.assertNotIn('processing_error',raw)
        self.assertEqual([x['decision'] for x in items if x['stage']=='R3'],['revise'])
    def test_neighbors_at_each_boundary_and_retry(self):
        script=['배경을 소개한다.','주장을 설명한다.','범위를 한정한다.']
        for n in (1,2,3):
            row=claim_context(claim(n),script)
            self.assertEqual(row['sentence'],dict(sent=n,text=script[n-1]))
            self.assertEqual(row['previous_sentence'],dict(sent=n-1,text=script[n-2]) if n>1 else None)
            self.assertEqual(row['next_sentence'],dict(sent=n+1,text=script[n]) if n<3 else None)
        class Retry(Responses):
            def call(self,stage,payload,schema,instruction,system=None):
                if stage.startswith('R3-d'):
                    self.calls.append((stage,payload,schema,instruction));return {'verdicts':[]}
                return super().call(stage,payload,schema,instruction,system)
        fake=Retry(claims=[claim(2)]);review(document('사실해설',script),fake)
        requests=[x[1] for x in fake.calls if x[0].startswith('R3-d')]
        self.assertEqual(len(requests),2);self.assertEqual(requests[0]['claims'],requests[1]['claims'])
        self.assertEqual(requests[0]['claims'][0]['previous_sentence']['text'],script[0])
        for n in (0,4,True):
            with self.assertRaises(ValueError):claim_context(claim(n),script)
    def test_severity_policy_and_schema(self):
        for verdict in ('overstated','contradicted','not_found','outdated'):
            for severity in ('major','minor'):
                with self.subTest(verdict=verdict,severity=severity):
                    d=document('사실해설');d['category']='C08'
                    items,raw,_=review(d,Responses(verdict=verdict,severity=severity,claims=[claim()]))
                    self.assertNotIn('processing_error',raw)
                    expected='pass_with_flags' if severity=='minor' and verdict!='contradicted' else 'revise'
                    self.assertEqual([x['decision'] for x in items if x['stage']=='R3'],[expected])
        fields=VERDICTS['properties']['verdicts']['items']
        self.assertIn('severity',fields['required']);self.assertEqual(fields['properties']['severity']['enum'],['major','minor'])
    def test_major_real_world_claims_revise_across_types(self):
        for typ in ('창작·각색','해석·감상','사실해설'):
            for verdict in ('not_found','outdated'):
                with self.subTest(typ=typ,verdict=verdict):
                    items,raw,_=review(document(typ),Responses(verdict=verdict,claims=[claim()]))
                    self.assertNotIn('processing_error',raw)
                    self.assertEqual([x['decision'] for x in items if x['stage']=='R3'],['revise'])
    def test_directions_distinguish_condition_and_added_detail(self):
        verdict=dict(verdict='overstated',evidence='s1',note='원문에 없는 설명',relevant_conditions=[],unsupported_detail='색을 자동으로 바꾼다')
        direction=r3_fix(verdict,[],claim())
        self.assertIn('색을 자동으로 바꾼다',direction);self.assertIn('삭제하거나',direction);self.assertNotIn('단정하지',direction)
        verdict.update(unsupported_detail='',relevant_conditions=['실내 측정에 한함'])
        self.assertEqual(r3_fix(verdict,[]),'빠진 조건·범위「실내 측정에 한함」를 해당 주장에 명시하기')
        verdict['relevant_conditions']=[];self.assertIn(claim()['text'],r3_fix(verdict,[],claim()))
    def test_prompt_preserves_hedge_scope_and_source_requirement(self):
        self.assertIn('같을 때만',R3_VERDICT_INSTRUCTION);self.assertIn('원문·quote의 대응 구절',R3_VERDICT_INSTRUCTION);self.assertIn('주석에만 있는 조건',R3_VERDICT_INSTRUCTION)

class Listening(unittest.TestCase):
    def test_local_directions_and_global_scope(self):
        hard=[dict(sent=1,kind='unclear_referent',severity='major',reason='지시어의 대상이 둘이다',fix='지시어를 첫 번째 도구의 이름으로 바꾸기'),dict(sent=2,kind='multiple_numbers',severity='minor',reason='수치 나열',fix='수치를 두 문장으로 나누기'),dict(sent=1,kind='structure',severity='major',reason='결말 배치',fix='결말 이동'),dict(sent=2,kind='style',severity='major',reason='말투 반복',fix='말투 변경')]
        fake=Responses(hard=hard,scores={'structure':2,'style':2})
        items,raw,_=review(document('사실해설',['먼저 도구를 골랐다.','이후 측정했다.']),fake)
        self.assertNotIn('processing_error',raw)
        rows=[x for x in items if x['stage']=='R5a'];local=[x for x in rows if x['scope']=='sentence'];global_rows=[x for x in rows if x['scope']=='script']
        self.assertEqual([(x['sent'],x['decision']) for x in local],[(1,'revise'),(2,'pass_with_flags')]);self.assertEqual(local[0]['direction'],hard[0]['fix'])
        self.assertEqual(len(global_rows),2);self.assertTrue(all(x['sent'] is None for x in global_rows));self.assertTrue(all('대본 전체' in x['direction'] for x in global_rows));self.assertEqual(raw['R5a']['named']['hard_sents'],hard)
    def test_global_hard_items_never_create_sentence_revise(self):
        fake=Responses(hard=[dict(sent=1,kind='style',severity='major',reason='전체 말투',fix='말투 변경')])
        items,raw,_=review(document('사실해설'),fake)
        self.assertNotIn('processing_error',raw);self.assertFalse([x for x in items if x['stage']=='R5a'])
        fields=QUALITY['properties']['hard_sents']['items']['required'];self.assertIn('kind',fields);self.assertIn('fix',fields);self.assertIn('문장 자체',R5A_INSTRUCTION)

class EntityAndDetail(unittest.TestCase):
    def test_private_business_uses_private_person_policy(self):
        entities=[dict(sent=i,text='가상 시험 식별자',kind=kind,identifiable=identified,negative=negative) for i,(kind,identified,negative) in enumerate([('private_business',True,False),('private_business',True,True),('institution',True,False),('fictional',False,False)],1)]
        items,raw,_=review(document(),Responses(entities=entities))
        self.assertNotIn('processing_error',raw);self.assertEqual([(x['sent'],x['decision']) for x in items if x['stage']=='R4'],[(1,'revise'),(2,'human_review')])
        self.assertIn('일반 일상 조언',R4_INSTRUCTION);self.assertIn('지금 운전 중 실행을 명시하지 않으면 제외',R4_INSTRUCTION)
    def test_same_detail_direction_lists_all_valid_sentences(self):
        rows=[dict(sent=1,category='title_hook',reason='동일 기록 반복',fix='기록을 일반화하기',related_sents=[3,1,3,0,999,True])]
        fake=Responses(r2=rows);items,raw,_=review(document(script=['기록은 아홉 회였다.','가격은 아홉 원이었다.','구 회 기록을 떠올렸다.']),fake)
        self.assertNotIn('processing_error',raw);direction=next(x['direction'] for x in items if x['stage']=='R2')
        self.assertEqual(direction,'문장 1, 3의 같은 세부를 함께 수정: 기록을 일반화하기')
        r2=next(x for x in fake.calls if x[0]=='R2-llm');self.assertIn('related_sents',r2[2]['properties']['items']['items']['required']);self.assertIn('다른 대상을 뜻하면 묶지 않는다',r2[3])
