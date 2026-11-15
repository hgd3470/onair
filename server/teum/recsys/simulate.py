"""가상 사용자로 전략을 비교하는 모의 실험. 실제 사용자 데이터가 아니다 (절차 점검용).

    python -m teum.recsys.simulate --data-root ../content/demo_data [--users 300 --rounds 8 --seed 7]

가정: 사용자마다 좋아하는 카테고리 1~2개(완청 확률 높음)가 있고, 콘텐츠마다 모두에게 통하는
완성도 차이(q)가 있다. 사용자의 절반만 관심사를 직접 고른다. 매 회차 각 사용자는 추천 상위
3편을 듣고, 완청하면 좋은 반응, 아니면 넘김으로 남는다. 다른 사용자 반응(bandit·ease)이
규칙 추천보다 도움이 되는지만 본다. 숫자는 가정에 따라 바뀌므로 근거 자료로 쓰지 않는다.
"""
import argparse
import random
from collections import defaultdict
from pathlib import Path

from teum.catalog.service import load_published
from teum.playlist.ranker import rank_candidates
from teum.playlist.topics import GROUPS
from teum.recsys import ease
from teum.recsys.signals import outcome
from teum.recsys.strategies import rerank


def make_world(stories, n_users, rng):
    cats = sorted({s["category"] for s in stories})
    quality = {s["content_id"]: rng.uniform(-0.2, 0.2) for s in stories}
    users = []
    for u in range(n_users):
        fav = rng.sample(cats, k=min(len(cats), rng.choice([1, 2])))
        pref = {c: (0.75 if c in fav else 0.2) for c in cats}
        declared = [fav[0]] if rng.random() < 0.5 else []
        users.append({"id": f"sim-{u}", "pref": pref, "interests": declared})
    return users, quality


def run(stories, strategy, n_users, rounds, seed, listen_per_round=3):
    rng = random.Random(seed)
    users, quality = make_world(stories, n_users, rng)
    play = random.Random(seed + 1)
    histories = {u["id"]: {} for u in users}
    model, totals = None, defaultdict(int)
    curve = []
    for r in range(rounds):
        stats = defaultdict(lambda: [0, 0])
        for h in histories.values():
            for cid, st in h.items():
                o = outcome(st["completed"], st["skipped"], st["liked"])
                if o is not None:
                    stats[cid][1 - o] += 1
        if strategy == "ease":
            model = ease.fit({u: {c for c, st in h.items() if st["completed"] or st["liked"]}
                              for u, h in histories.items()}, lam=10.0)
        done = ok = 0
        for u in users:
            h = histories[u["id"]]
            groups = {g for g, codes in GROUPS.items() if codes.intersection(u["interests"])}
            ranked, _ = rank_candidates(stories, h, u["interests"], groups)
            liked = [c for c, st in h.items() if st["completed"] or st["liked"]]
            ranked, _, _ = rerank(strategy, ranked, groups, stats=stats, rng=random.Random(rng.random()),
                                  model=model, liked=liked)
            for c in ranked[:listen_per_round]:
                p = min(0.95, max(0.02, u["pref"][c["category"]] + quality[c["content_id"]]))
                completed = play.random() < p
                h[c["content_id"]] = {"played_sec": 30, "completed": completed, "skipped": not completed,
                                      "liked": completed and play.random() < 0.3, "saved": False}
                done += 1
                ok += completed
        totals["listened"] += done
        totals["completed"] += ok
        curve.append(round(ok / max(1, done), 3))
    return {"completion_rate": round(totals["completed"] / max(1, totals["listened"]), 3), "by_round": curve}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--users", type=int, default=300)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--seeds", type=int, default=5)
    args = ap.parse_args(argv)
    rows = load_published(Path(args.data_root) / "outputs" / "catalog.sqlite")
    stories = [{**r, "audio_uri": r["audio_uri"]} for r in rows if r["kind"] == "story"]
    print(f"가상 실험 (실제 사용자 아님): 이야기 {len(stories)}편, 사용자 {args.users}명, "
          f"{args.rounds}회차, 시드 {args.seeds}개 평균")
    for strategy in ("rule", "bandit", "ease"):
        res = [run(stories, strategy, args.users, args.rounds, s) for s in range(args.seeds)]
        mean = sum(r["completion_rate"] for r in res) / len(res)
        sd = (sum((r["completion_rate"] - mean) ** 2 for r in res) / len(res)) ** 0.5
        last = sum(r["by_round"][-1] for r in res) / len(res)
        print(f"  {strategy:6s} 완청률 {mean:.3f} ± {sd:.3f}   마지막 회차 {last:.3f}")


if __name__ == "__main__":
    main()
