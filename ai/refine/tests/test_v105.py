import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_v104 import Fake, document
from teum_refine.__main__ import main
from teum_refine.llm import review
from teum_refine.prompts import render


class Judge(Fake):
    def __init__(self, harmful=False, fail=None):
        super().__init__()
        self.harmful=harmful
        self.fail=fail

    def call(self, stage, payload, schema, instruction, system=None):
        if stage==self.fail:
            raise RuntimeError('fixture failure')
        result=super().call(stage,payload,schema,instruction,system)
        if stage=='R4' and self.harmful:
            result['harmful']=[dict(sent=1,reason='판정된 유해 사용')]
        return result


class V105(unittest.TestCase):
    def run_cli(self, fake=None, sentence='새끼가 잠들었습니다.'):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);inp=p/'in.json';out=p/'out'
            inp.write_text(json.dumps(document(script=[sentence])))
            args=['teum_refine','run',str(inp),'--out',str(out),'--skip-safety','--skip-dedup']
            if fake is None:args.append('--skip-llm')
            with patch('sys.argv',args),patch('teum_refine.__main__.Client',return_value=SimpleNamespace(calls=[])),patch('teum_refine.__main__.review',side_effect=lambda d,c,comparison: review(d,fake,comparison)),contextlib.redirect_stdout(io.StringIO()):
                main()
            return json.loads((out/'audit.refine.json').read_text())

    def test_harmless_and_version(self):
        for failure in (None,'R4c'):
            r=self.run_cli(Judge(fail=failure))
            self.assertFalse([x for x in r['items'] if x['stage']=='R4'])
            if failure is None:self.assertNotEqual(r['decision'],'human_review')
            self.assertEqual(r['pipeline_version'],'refine-v1.0.5')

    def test_harmful_no_duplicate(self):
        r=self.run_cli(Judge(harmful=True))
        hits=[x for x in r['items'] if x['stage']=='R4']
        self.assertEqual(len(hits),1)
        self.assertEqual((hits[0]['reason'],hits[0]['decision']),('판정된 유해 사용','human_review'))

    def test_skip_keeps_candidate(self):
        r=self.run_cli()
        self.assertTrue(any(x['reason'].startswith('유해 표현: ') and x['decision']=='human_review' for x in r['items']))

    def test_failed_judge_keeps_candidate(self):
        r=self.run_cli(Judge(fail='R4'))
        self.assertTrue(any(x['reason'].startswith('유해 표현: ') for x in r['items']))

    def test_sources_and_xml(self):
        for typ in ('사실해설','창작·각색'):
            for text in ('원문','가'*60000,'나'*60001,''):
                for comparison in (False,True):
                    fake=Judge()
                    _,raw,_=review(document(typ,source=text),fake,comparison=comparison)
                    self.assertNotIn('processing_error',raw)
                    stage,payload,_,instruction=next(x for x in fake.calls if x[0]=='R4c')
                    if text:
                        source=payload['sources'][0]
                        self.assertEqual(source['text'],text[:60000])
                        self.assertEqual(source.get('truncated',False),len(text)>60000)
                    else:self.assertEqual(payload['sources'],[])
                    prompt,system=render('xml-ko',stage,payload,instruction)
                    self.assertIn('<sources>',prompt)
                    self.assertNotIn('<sources>',system)

    def test_driving_candidate(self):
        sentence='그는 눈을 감았습니다.'
        for fake,expected in ((Judge(),False),(None,True)):
            r=self.run_cli(fake,sentence)
            self.assertEqual(any(x['reason']=='운전 중 위험 동작 지시' for x in r['items']),expected)


class R2PolicyTest(unittest.TestCase):
 def test_detail_combination_only_warns(self):
  from teum_refine.llm import R2_POLICY
  self.assertEqual(R2_POLICY['specific_combination'],'pass_with_flags')
  for category in ('title_hook','money','identifying_name'):self.assertEqual(R2_POLICY[category],'revise')
