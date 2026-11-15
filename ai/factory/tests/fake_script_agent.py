"""시험용 생성기. script-agent와 같은 명령(run, revise)을 받지만 모델을 부르지 않는다.

run     입력함의 소재마다 TEUM_FAKE_SAMPLES 폴더의 실제 대본 JSON 한 편을 output/으로 내보낸다
revise  revision을 올린다. TEUM_FAKE_SAMPLES/fixed/<content_id>.json이 있으면 그 내용으로 고친다. 한도(2)를 넘으면 진짜처럼 revision_budget_exhausted
"""
import argparse
import json
import os
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--root", required=True)
ap.add_argument("--env-file")
sub = ap.add_subparsers(dest="command", required=True)
r = sub.add_parser("run")
r.add_argument("--limit", type=int, default=100)
v = sub.add_parser("revise")
v.add_argument("output")
v.add_argument("feedback")
v.add_argument("--expected-version", required=True)
a = ap.parse_args()
root = Path(a.root)

if a.command == "run":
    samples = sorted(Path(os.environ["TEUM_FAKE_SAMPLES"]).glob("*.json"))
    used = {p.name.lower() for p in (root / "output").glob("*.json")}
    results = []
    for src in sorted((root / "input").iterdir())[:a.limit]:
        sample = next((s for s in samples if s.name.lower() not in used), None)
        if sample is None:
            results.append({"filename": src.name, "status": "failed", "code": "no_sample"})
            continue
        d = json.loads(sample.read_text(encoding="utf-8"))
        d["generator"]["revision"] = 0
        d["generator"].pop("feedback_revision", None)
        d["version"] = "1.0.0"
        d["status"] = "hold" if "hold" in src.name else "ready_for_downstream_review"
        out = root / "output" / f"{d['content_id']}.json"
        out.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        used.add(sample.name.lower())
        src.rename(root / "archive" / src.name)
        results.append({"filename": src.name, "status": d["status"], "content_id": d["content_id"]})
    print(json.dumps(results, ensure_ascii=False))
    sys.exit(2 if any(x["status"] == "failed" for x in results) else 0)

out = Path(a.output)
d = json.loads(out.read_text(encoding="utf-8"))
row = next((json.loads(x) for x in Path(a.feedback).read_text(encoding="utf-8").splitlines()
            if x.strip() and json.loads(x).get("script_id") == d["content_id"]), None)
err = None
if row is None or not row.get("items"):
    err = "invalid_feedback"
elif row.get("decision") != "revise":
    err = "feedback_requires_human_review"
elif d["version"] != a.expected_version:
    err = "feedback_version_mismatch"
elif d["generator"]["revision"] >= 2:
    err = "revision_budget_exhausted"
if err:
    print(json.dumps({"error_type": "FeedbackError", "code": err}), file=sys.stderr)
    sys.exit(1)
d["generator"]["revision"] += 1
d["version"] = f"1.0.{d['generator']['revision']}"
fixed = Path(os.environ["TEUM_FAKE_SAMPLES"]) / "fixed" / f"{d['content_id']}.json"
if fixed.exists():  # 지적을 고친 판이 준비돼 있으면 그 내용으로 바꾼다
    for k, val in json.loads(fixed.read_text(encoding="utf-8")).items():
        if k not in ("content_id", "generator", "version", "input_sha256"):
            d[k] = val
else:
    d["script"][0] = d["script"][0] + " "  # 고치지 못한 채 판만 바뀐 경우
d["status"] = "ready_for_downstream_review"
out.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"content_id": d["content_id"], "status": d["status"], "revision": d["generator"]["revision"]}))
