"""전략별 성적표: 노출 기록과 청취 기록을 이어 붙여 본다.

    python -m teum.recsys.evaluate --events <events.sqlite> [--serve <recsys_serve.sqlite>]

지표(이야기만): 노출 수, 재생 시작률, 완청률, 넘김률, 좋아요·보관률, 서로 다른 콘텐츠 비율(coverage).
청취 기록은 콘텐츠별 최종 상태라서 "몇 번째 노출 때 들었는지"까지는 못 가른다. 같은 사용자가
여러 전략을 받으면 섞이므로 비교 실험은 TEUM_RECSYS_SPLIT(사용자 단위 나눔)으로 돌린다.
"""
import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from teum.recsys.serve_log import ServeLog


def report(events_db, serve_db):
    state = {}
    if Path(events_db).exists():
        db = sqlite3.connect(f"file:{events_db}?mode=ro", uri=True)
        for u, c, played, completed, skipped, liked, saved in db.execute(
                "SELECT user_id, content_id, played_sec, completed, skipped, liked, saved FROM listen_state"):
            state[(u, c)] = (played or 0, completed, skipped, liked, saved)
        db.close()
    seen = defaultdict(set)
    acc = defaultdict(lambda: defaultdict(int))
    for row in ServeLog(serve_db).read():
        algo = row["algorithm"]
        acc[algo]["requests"] += 1
        for item in json.loads(row["items"]):
            if item["kind"] != "story":
                continue
            key = (row["user_id"], item["content_id"])
            if key in seen[algo]:
                continue
            seen[algo].add(key)
            played, completed, skipped, liked, saved = state.get(key, (0, 0, 0, 0, 0))
            a = acc[algo]
            a["served"] += 1
            a["started"] += played > 0
            a["completed"] += bool(completed)
            a["skipped"] += bool(skipped) and not completed
            a["liked_or_saved"] += bool(liked or saved)
    out = {}
    for algo, a in acc.items():
        n = max(1, a["served"])
        out[algo] = {"requests": a["requests"], "served_stories": a["served"],
                     "start_rate": round(a["started"] / n, 3), "completion_rate": round(a["completed"] / n, 3),
                     "skip_rate": round(a["skipped"] / n, 3), "like_save_rate": round(a["liked_or_saved"] / n, 3),
                     "coverage": len({c for _, c in seen[algo]})}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--serve")
    args = ap.parse_args(argv)
    serve = args.serve or Path(args.events).parent / "recsys_serve.sqlite"
    print(json.dumps(report(args.events, serve), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
