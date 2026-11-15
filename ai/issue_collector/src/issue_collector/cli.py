import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import re
import sys
import tomllib
from .core import now, target_day

def main():
    parser=argparse.ArgumentParser(description='이슈 수집 에이전트')
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2])
    sub=parser.add_subparsers(dest='command',required=True)
    collect=sub.add_parser('collect',help='KST 전날 이슈/국내 시장 수집')
    collect.add_argument('--date',help='대상 사건일 YYYY-MM-DD; 기본 한국시간 전날')
    collect.add_argument('--streams',nargs='+',choices=['issue','market'],default=['issue','market'])
    collect.add_argument('--count',type=int,default=3)
    collect.add_argument('--run-id',default=None)
    collect.add_argument('--resume',action='store_true')
    collect.add_argument('--mode',choices=['standard','batch'],help='config.toml collection.mode 재정의')
    inbox_run=sub.add_parser('run',help='총개수 균형 수집 후 생성기 입력함 전달 (factory용)')
    inbox_run.add_argument('--date',help='대상 사건일 YYYY-MM-DD; 기본 한국시간 전날')
    inbox_run.add_argument('--limit',type=int,default=5,help='전체 인계 소재 상한 1~40; 홀수의 추가 1편은 시장 (기본 5)')
    inbox_run.add_argument('--inbox',type=Path,help='입력함; 기본 TEUM_INBOX → TEUM_HOME/script_agent/input → 인접 script_agent/input')
    inbox_run.add_argument('--run-id',default='inbox-daily-v1',help='날짜별 실행 ID; 동일 계약은 재사용 (기본 inbox-daily-v1)')
    inbox_run.add_argument('--dry-run',action='store_true',help='배분·경로만 JSON 출력; API·인증 읽기·파일 쓰기 없음')
    inbox_run.add_argument('--mode',choices=['standard','batch'],help='config.toml collection.mode 재정의')
    demo=sub.add_parser('demo',help='완료된 수집 결과로 기존 script_agent 실제 실행')
    demo.add_argument('--date',required=True)
    demo.add_argument('--run-id',required=True)
    demo.add_argument('--demo-id',default=None)
    demo.add_argument('--resume',action='store_true')
    demo.add_argument('--streams',nargs='+',choices=['issue','market'],default=['issue','market'])
    demo.add_argument('--count',type=int,default=3,help='분야별 대본 수; 단일 실검증은 --streams issue --count 1')
    assess=sub.add_parser('assess-use',help='기존 데모 사실 재서술 및 수집·처리 권한 분리 검토')
    assess.add_argument('--demo-id',required=True)
    assess.add_argument('--topics',nargs='+',help='지정한 주제만 재검토; 나머지는 해시가 유효한 기존 검토 보존')
    publication=sub.add_parser('publish-license',help='기존 데모 대본에 팀 저작권·권리 유보 기록; API 호출·게시 없음')
    publication.add_argument('--demo-id',required=True)
    args=parser.parse_args()
    root=args.root.resolve()
    config=tomllib.loads((root/'config.toml').read_text())
    if getattr(args,'mode',None): config['collection']['mode']=args.mode
    try:
        if args.command=='run':
            from .inbox import inbox_path, run_to_inbox
            # Factory gets one JSON object on stdout; human progress is stderr.
            with redirect_stdout(sys.stderr):
                report, code=run_to_inbox(root,config,target_day(args.date),args.limit,
                    inbox_path(root,args.inbox),args.run_id,dry_run=args.dry_run)
            print(json.dumps(report,ensure_ascii=False))
            return code
        if args.command=='publish-license':
            from .publication import publish_demo
            return publish_demo(root,args.demo_id)
        if args.command=='collect':
            if not 1<=args.count<=20: raise ValueError('count_out_of_range')
            rid=args.run_id or now().replace(':','').replace('+','-')
            if not re.fullmatch(r'[A-Za-z0-9_-]+',rid): raise ValueError('unsafe_run_id')
            from .pipeline import run
            result,folder=run(root,config,target_day(args.date),list(dict.fromkeys(args.streams)),args.count,rid,args.resume)
            print(json.dumps({'status':result['status'],'path':str(folder),'counts':{s:len(v['items']) for s,v in result['streams'].items()}},ensure_ascii=False))
            return 0 if result['status']=='complete' else 2
        if args.command=='assess-use':
            from .use_review import assess_demo
            return assess_demo(root,config,args.demo_id,args.topics)
        from .demo import build_demo
        return build_demo(root,config,args)
    except Exception as exc:
        # Raw provider exceptions can contain credentials. Only our bounded codes are printed.
        code=str(exc) if isinstance(exc,ValueError) and re.fullmatch('[a-z0-9_]+',str(exc)) else type(exc).__name__
        print(json.dumps({'status':'failed','code':code}))
        return 1

if __name__=='__main__':
    raise SystemExit(main())
