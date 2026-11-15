"""새벽 일괄 작업: 청취 기록으로 EASE 모델을 학습해 JSON으로 저장한다. CPU만 쓴다.

    python -m teum.recsys.train --events <events.sqlite> [--out <recsys_ease.json>] [--lam 10]

서버는 파일이 바뀐 것을 보고 다음 요청부터 새 모델을 읽는다. 데이터가 기준
(사용자 20명, 좋은 반응 100건)보다 적으면 모델은 저장되지만 ease 전략은 bandit으로 넘어간다.
"""
import argparse
import time
from pathlib import Path

from teum.recsys import ease, signals


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--out")
    ap.add_argument("--lam", type=float, default=10.0)
    args = ap.parse_args(argv)
    out = Path(args.out or Path(args.events).parent / "recsys_ease.json")
    start = time.time()
    model = ease.fit(signals.positives(args.events), lam=args.lam)
    model["trained_at"] = time.time()
    ease.save(model, out)
    print(f"EASE: users {model['users']}, positives {model['positives']}, items {len(model['items'])}, "
          f"{time.time() - start:.2f}s → {out}")


if __name__ == "__main__":
    main()
