"""Real review/revision diagnostics on authored drafts; no source analysis or draft generation."""
import argparse
import json
from pathlib import Path

from .io import atomic_json, load_config, now
from .models import Draft, Material, SourceMeta
from .pipeline import Pipeline
from .providers import credentials
from .validation import checks


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--env-file', type=Path)
    p.add_argument('--root', type=Path, default=Path.cwd())
    p.add_argument('--version')
    p.add_argument('--case', choices=['adaptation', 'explanation', 'instruction', 'interpretation', 'narrative', 'reading', 'card_correction'])
    args = p.parse_args()
    config = load_config(args.root)
    if args.version: config['pipeline']['prompt_version'] = args.version
    folder = args.root / 'experiments' / ('reviewer-' + config['pipeline']['prompt_version'])
    if args.case:
        folder = folder.with_name(folder.name + '-' + args.case)
    summary = {'kind': 'real_review_and_revision_only', 'started_at': now(),
               'model': config['models']['reviewer'], 'revision_model': config['models']['generator'],
               'draft_generation_executed': False,
               'quality_claim': 'diagnostic adversarial fixtures; not human listening validation', 'cases': []}
    for path in sorted((args.root / 'examples/reviewer_cases').glob('*.json')):
        if args.case and path.stem != args.case:
            continue
        case = json.loads(path.read_text())
        pipe = Pipeline(args.root, config, credentials(args.env_file, root=args.root))
        text, meta = case['source_text'], SourceMeta.model_validate(case['metadata'])
        material, draft = Material.model_validate(case['material']), Draft.model_validate(case['draft'])
        base = {'source_text':text,'metadata':meta.model_dump(),'material':material.model_dump(),
                'target_sec':meta.target_sec,'target_chars':round(meta.target_sec*config['pipeline']['chars_per_sec'])}
        final, review, revision, history, material = pipe.review_loop(base,draft,material,text,meta,folder/case['case_id'])
        result = {'case_id':case['case_id'], 'production_type':material.production_type,
                  'expected_issue':case['expected_issue'],'initial_review':history[0],
                  'final_review':history[-1], 'revision':revision,'final':final.model_dump(),
                  'final_material':material.model_dump(),
                  'deterministic_remaining':[x.model_dump() for x in checks(final,material,text,meta,config['pipeline']['chars_per_sec'],config['pipeline'].get('character_count_mode','non_whitespace'))],
                  'provenance':'Manually authored faulty fixture; configured generator revises and reviewer checks; no generated draft',
                  'calls':pipe.events}
        atomic_json(folder / (case['case_id']+'-summary.json'),result)
        summary['cases'].append({'case_id':case['case_id'],'initial_verdict':history[0]['verdict'],
                                 'final_verdict':review.verdict,'revision':revision,
                                 'remaining_count':len(result['deterministic_remaining'])})
        atomic_json(folder/'summary.json',summary)
        print(json.dumps(summary['cases'][-1]),flush=True)
    summary['finished_at']=now()
    atomic_json(folder/'summary.json',summary)

if __name__ == '__main__': main()
