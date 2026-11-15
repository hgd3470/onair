"""Command-line interface for the Source Collection Harness."""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sys
import yaml

from .config import CATEGORIES, DEFAULT_CSV_KO, LICENSES, SCRIPT_AGENT_ROOT, SOURCE_COLLECTOR_ROOT
from .harness import CollectionHarness
from .judge import HeadlessJudge
from .models import CandidateSource
from .prescreen import Deduplicator
from .privacy import PrivacyViolation, require_private_data_free, require_safe_candidate


def parse_categories(cat_arg: str) -> list[str]:
    if not cat_arg or cat_arg.strip().lower() == "all":
        return list(CATEGORIES.keys())
    parts = [p.strip().upper() for p in cat_arg.split(",") if p.strip()]
    for p in parts:
        if p not in CATEGORIES:
            raise ValueError(f"Invalid category code: {p}. Valid codes: C01~C21 or 'all'.")
    return parts


def cmd_run(args: argparse.Namespace) -> int:
    categories = parse_categories(args.categories)
    output_dir = Path(args.output_dir).resolve() if args.output_dir else None

    harness = CollectionHarness(
        mode=args.mode,
        categories=categories,
        count_per_category=args.count,
        batch_id=args.batch_id,
        model=args.model,
        min_score=args.min_score,
        max_workers=args.workers,
        timeout=args.timeout,
        output_dir=output_dir,
        mock=args.mock,
    )
    summary = harness.run()
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["total_accepted"] == len(categories) * args.count else 1


def cmd_prescreen(args: argparse.Namespace) -> int:
    dedup = Deduplicator()
    cat = args.category.upper()
    res = dedup.check_title(args.title)
    if res.is_valid and args.url:
        res = dedup.check_url(args.url, cat)
    if res.is_valid and args.id in dedup.existing_ids:
        from .models import PreScreenResult
        res = PreScreenResult(False, "id_duplicate", args.id)
    out = {
        "valid": res.is_valid,
        "duplicate_type": res.duplicate_type,
        "matched_entry": res.matched_entry,
        "reason": res.reason,
        "db_stats": dedup.stats(),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if res.is_valid else 2


def cmd_evaluate(args: argparse.Namespace) -> int:
    md_path = Path(args.path).resolve()
    if not md_path.exists():
        print(json.dumps({"error": f"File not found: {md_path}"}, ensure_ascii=False))
        return 1

    raw = md_path.read_text(encoding="utf-8")
    require_private_data_free(raw)
    parts = raw.split("---", 2)
    if len(parts) < 3:
        print(json.dumps({"error": "Invalid Markdown frontmatter format"}, ensure_ascii=False))
        return 1

    fm = yaml.safe_load(parts[1])
    body = parts[2].strip()
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    title = lines[0].lstrip("#").strip() if lines and lines[0].startswith("#") else fm.get("source_id", "Untitled")
    text_body = "\n".join(lines[1:]) if lines and lines[0].startswith("#") else body

    cand = CandidateSource(
        id=fm.get("source_id", md_path.stem),
        category=fm.get("category", "C01"),
        title=title,
        url=fm.get("source_url") or "",
        author=fm.get("author", ""),
        source_id_ref="SRC-021",
        source_name="",
        production_type=fm.get("production_type", "창작·각색형"),
        license_kind=fm.get("license", {}).get("kind", ""),
        license_info=fm.get("license", {}),
        attribution=fm.get("attribution", ""),
        summary=text_body[:120],
        text=text_body,
    )

    judge = HeadlessJudge(model=args.model, min_score=args.min_score, timeout=args.timeout, mock=args.mock)
    if args.elevate:
        cand = judge.evaluate_and_ensure(cand)
    else:
        sc, reas = judge.evaluate(cand)
        cand.score = sc
        cand.eval_reason = reas

    require_safe_candidate(cand)
    out = {
        "source_id": cand.id,
        "category": cand.category,
        "title": cand.title,
        "score": cand.score,
        "passed": cand.score >= args.min_score,
        "reason": cand.eval_reason,
        "characters": len(cand.text),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if cand.score >= args.min_score else 2


def cmd_status(args: argparse.Namespace) -> int:
    dedup = Deduplicator()
    cat_counts = Counter()
    score_counts = Counter()
    total_rows = 0

    csv_target = DEFAULT_CSV_KO if DEFAULT_CSV_KO.exists() else (SOURCE_COLLECTOR_ROOT / "전체_소스_목록.csv")
    if csv_target.exists():
        with open(csv_target, "r", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                total_rows += 1
                cat_counts[r.get("카테고리_코드", "")] += 1
                score_counts[r.get("AI_평가점수", "")] += 1

    library_count = sum(p.name != "collection-log.md" for p in (SOURCE_COLLECTOR_ROOT / "output").glob("*/*.md"))
    input_count = len([p for p in (SCRIPT_AGENT_ROOT / "input").glob("*.md") if p.is_file()]) if (SCRIPT_AGENT_ROOT / "input").exists() else 0

    status_info = {
        "master_csv_path": str(csv_target),
        "total_registered_sources": total_rows,
        "directory_counts": {
            "output_sources": library_count,
            "input_active": input_count,
        },
        "dedup_engine_stats": dedup.stats(),
        "score_distribution": dict(score_counts),
        "category_counts": {k: cat_counts[k] for k in sorted(cat_counts.keys())},
    }
    print(json.dumps(status_info, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="source-collector",
        description="Hyundai Pleos Audio Short-form Source Collection & LLM-as-a-Judge Harness",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # 1. run
    p_run = sub.add_parser("run", help="Run the 3-step source collection & evaluation pipeline")
    p_run.add_argument("--mode", choices=["staging", "direct", "sandbox"], default="staging", help="Target mode: staging (good/), direct (input/), or sandbox (isolated test)")
    p_run.add_argument("--categories", default="all", help="Comma-separated category codes (e.g. C01,C13) or 'all'")
    p_run.add_argument("--count", type=int, default=20, help="Number of sources to collect per category")
    p_run.add_argument("--batch-id", default=None, help="Custom Batch ID (default: current KST YYMMDD-HHMM)")
    p_run.add_argument("--model", default="gemini-3.8-flash-medium", help="Headless evaluation model")
    p_run.add_argument("--min-score", type=int, default=9, help="Cutoff score out of 10 (default: 9)")
    p_run.add_argument("--workers", type=int, default=6, help="Parallel worker threads (default: 6)")
    p_run.add_argument("--timeout", type=int, default=80, help="Headless CLI timeout in seconds (default: 80)")
    p_run.add_argument("--output-dir", default=None, help="Custom output directory for .md files")
    p_run.add_argument("--mock", action="store_true", help="Run in offline deterministic mock mode")
    p_run.add_argument("--json", action="store_true", help="Print full JSON summary output")

    # 2. prescreen
    p_pre = sub.add_parser("prescreen", help="Pre-screen a candidate title/URL for duplication")
    p_pre.add_argument("--title", required=True, help="Candidate topic/title to check")
    p_pre.add_argument("--url", default="", help="Candidate source URL")
    p_pre.add_argument("--category", default="C01", help="Category code (C01~C21)")
    p_pre.add_argument("--id", default=None, help="Optional source ID to check")

    # 3. evaluate
    p_eval = sub.add_parser("evaluate", help="Evaluate a Markdown source file using headless LLM-as-a-Judge")
    p_eval.add_argument("path", help="Path to Markdown source file")
    p_eval.add_argument("--model", default="gemini-3.8-flash-medium", help="Headless model")
    p_eval.add_argument("--min-score", type=int, default=9, help="Cutoff score (default: 9)")
    p_eval.add_argument("--timeout", type=int, default=80, help="Timeout in seconds")
    p_eval.add_argument("--elevate", action="store_true", help="Automatically elevate & re-evaluate if score < min-score")
    p_eval.add_argument("--mock", action="store_true", help="Use mock evaluator")

    # 4. status
    sub.add_parser("status", help="Show collection ledger and directory statistics")

    args = parser.parse_args(argv)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "prescreen":
        return cmd_prescreen(args)
    if args.command == "evaluate":
        try:
            return cmd_evaluate(args)
        except PrivacyViolation as exc:
            print(json.dumps({"passed": False, "error": "privacy_blocked", "rules": exc.codes}))
            return 2
    if args.command == "status":
        return cmd_status(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
