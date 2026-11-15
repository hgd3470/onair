"""제작 파이프라인 명령행."""
import argparse
from .pipeline import Pipeline


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("ingest", "tts-jobs", "tts-ingest", "review", "redo", "publish", "status"):
        sub = commands.add_parser(name)
        sub.add_argument("--work", required=True)
        if name == "ingest":
            sub.add_argument("files", nargs="+")
        elif name == "tts-jobs":
            sub.add_argument("--out", required=True)
            sub.add_argument("--speaker", default="teum_063")  # 투표로 정한 기본 목소리(30대 남성 대화체)
            sub.add_argument("--language", default="Auto")  # 파인튜닝 체크포인트는 언어 구분 없이 학습됨
        elif name == "tts-ingest":
            sub.add_argument("results")
        elif name == "redo":
            sub.add_argument("content_id")
            sub.add_argument("--note", default="")
        elif name == "review":
            sub.add_argument("content_id")
            choice = sub.add_mutually_exclusive_group(required=True)
            choice.add_argument("--approve", action="store_true")
            choice.add_argument("--reject", action="store_true")
            sub.add_argument("--note", default="")
        elif name == "publish":
            sub.add_argument("--data-root", required=True)
    args = parser.parse_args(argv)
    pipeline = Pipeline(args.work)
    try:
        if args.command == "ingest":
            pipeline.ingest(args.files)
        elif args.command == "tts-jobs":
            pipeline.jobs(args.out, args.speaker, args.language)
        elif args.command == "tts-ingest":
            pipeline.results(args.results)
        elif args.command == "review":
            pipeline.review(args.content_id, args.approve, args.note)
        elif args.command == "redo":
            pipeline.redo(args.content_id, args.note)
        elif args.command == "publish":
            pipeline.publish(args.data_root)
        else:
            pipeline.status()
    except Exception as exc:
        pipeline.log(getattr(args, "content_id", None), args.command, False, str(exc))
        raise
    finally:
        pipeline.db.commit()
        pipeline.db.close()


if __name__ == "__main__":
    main()
