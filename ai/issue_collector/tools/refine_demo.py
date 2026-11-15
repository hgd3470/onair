#!/usr/bin/env python3
"""Apply explicit, version-checked editorial feedback via script_agent; no counter reset."""
import argparse
import json
import sys
from pathlib import Path
from filelock import FileLock
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT.parent/'script_agent/src')]
from issue_collector.core import digest,now,read,save
from issue_collector.llm import credentials
from issue_collector.publication import apply_output_license
from issue_collector.demo import describe_demo
from script_agent.fact_source import is_independent_fact_source
from script_agent.models import Issue,Handoff
from script_agent.pipeline import Pipeline
from script_agent.providers import ProviderError


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--demo',required=True,type=Path)
    parser.add_argument('--topic',required=True)
    parser.add_argument('--feedback',required=True,type=Path)
    parser.add_argument('--expected-version',required=True)
    args=parser.parse_args()
    demo=args.demo.resolve()
    if not demo.is_relative_to((ROOT/'demo').resolve()):raise ValueError('demo_outside_collector')
    with FileLock(str(demo/'.lock'),timeout=3600):
        manifest=read(demo/'manifest.json')
        rows=[x for x in manifest['scripts'] if x['id']==args.topic]
        if len(rows)!=1 or rows[0]['status']=='failed':raise ValueError('unique_completed_script_required')
        path=demo/rows[0]['output']
        before=read(path)
        issues=[Issue.model_validate(x) for x in read(args.feedback)]
        contract=read(demo/'contract.json')
        config=contract['script_config']
        audit=demo/'feedback-audit'/digest({'topic':args.topic,'version':args.expected_version,'issues':[x.model_dump() for x in issues]})[:20]
        if (audit/'result.json').exists():
            print('Feedback already completed; not called again.');return 0
        if (audit/'request.json').exists():raise ValueError('previous_feedback_unfinished_inspect_logs')
        if before['version']!=args.expected_version:raise ValueError('feedback_version_mismatch')
        save(audit/'request.json',{'origin':'coding_agent_quality_review','expected_version':args.expected_version,
            'issues':[x.model_dump() for x in issues],'at':now()})
        save(audit/'before.json',before)
        try:
            after=Pipeline(ROOT.parent/'script_agent',config,credentials(ROOT)).apply_feedback(path,issues,expected_version=args.expected_version)
        except ProviderError as exc:
            save(audit/'error.json',{'code':exc.code,'status':exc.status,'at':now()})
            print('Feedback failed: '+exc.code);return 2
        assert after['generator']['revision']==before['generator']['revision']
        assert after['generator']['feedback_revision']==before['generator']['feedback_revision']+1
        after=apply_output_license(after)
        if is_independent_fact_source(after['input_metadata']):
            after['status']='hold'
            after['release_status']='pending_content_review'
        Handoff.model_validate(after)
        save(path,after)
        (path.parent/'script.txt').write_text(after['title']+'\n\n'+'\n'.join(after['script'])+'\n')
        for item in manifest['scripts']:
            value=read(demo/item['output'])
            item.update(title=value['title'],status=value['status'],review=value['self_review'][-1]['verdict'],
                rights_status=value['rights_status'],revision=value['generator']['revision'],
                feedback_revision=value['generator'].get('feedback_revision',0),version=value['version'],
                sentences=len(value['script']),estimated_sec=value['duration']['estimated_sec'])
        manifest['updated_at']=now()
        save(demo/'manifest.json',manifest)
        lines=[f'이슈 수집 에이전트 데모 | {manifest["target_date"]}',describe_demo(manifest['scripts']),
               '권리 확인·사람 승인 전이며 음성 합성은 하지 않음.','']
        for item in manifest['scripts']:
            lines.append(f"{item['stream']} / {item['title']}: 자체검토 {item['review']}, {item['status']}, 추정 {item['estimated_sec']}초, 내부 수정 {item['revision']}·외부 피드백 {item['feedback_revision']}")
        (demo/'summary.txt').write_text('\n'.join(lines)+'\n')
        save(audit/'result.json',{'version':after['version'],'review':after['self_review'][-1]['verdict'],
            'internal_revision':after['generator']['revision'],'feedback_revision':after['generator']['feedback_revision'],
            'at':now()})
        print(json.dumps(read(audit/'result.json'),ensure_ascii=False))
        return 0 if after['self_review'][-1]['verdict']=='pass' else 2

if __name__=='__main__':
    raise SystemExit(main())
