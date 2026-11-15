"""Main orchestration harness for end-to-end source collection, evaluation, and registration."""

import concurrent.futures
from datetime import datetime, timezone, timedelta
from pathlib import Path
import shutil
import time
from typing import Any, Dict, List, Optional
from .config import (
    CATEGORIES,
    DEFAULT_CSV_EN,
    DEFAULT_CSV_KO,
    SCRIPT_AGENT_ROOT,
    SOURCE_COLLECTOR_ROOT,
)
from .generator import SourceGenerator
from .judge import HeadlessJudge
from .ledger import LedgerManager
from .models import CandidateSource
from .prescreen import Deduplicator
from .writer import SourceWriter
from .privacy import PrivacyViolation, require_safe_candidate

KST = timezone(timedelta(hours=9))


class CollectionHarness:
    """Executes the 3-step SOP defined in SOURCE_COLLECTION_GUIDE.md."""

    def __init__(
        self,
        mode: str = "staging",
        categories: Optional[List[str]] = None,
        count_per_category: int = 20,
        batch_id: Optional[str] = None,
        model: str = "gemini-3.8-flash-medium",
        min_score: int = 9,
        max_workers: int = 6,
        timeout: int = 80,
        output_dir: Optional[Path] = None,
        csv_paths: Optional[List[Path]] = None,
        source_library_root: Optional[Path] = None,
        mock: bool = False,
    ):
        if mode not in {"direct", "staging", "sandbox"}:
            raise ValueError(f"Invalid mode: {mode}. Must be 'direct', 'staging', or 'sandbox'.")
        self.mode = mode
        self.categories = categories or list(CATEGORIES.keys())
        for c in self.categories:
            if c not in CATEGORIES:
                raise ValueError(f"Unknown category code: {c}")

        self.count_per_category = count_per_category
        self.batch_id = batch_id or datetime.now(KST).strftime("%y%m%d-%H%M")
        self.model = model
        self.min_score = min_score
        self.max_workers = max_workers
        self.timeout = timeout
        self.mock = mock

        # Resolve target directories
        if output_dir is not None:
            self.target_dir = output_dir
        elif mode == "direct":
            self.target_dir = SCRIPT_AGENT_ROOT / "input"
        elif mode == "staging":
            self.target_dir = SCRIPT_AGENT_ROOT / "good"
        else:  # sandbox
            self.target_dir = SOURCE_COLLECTOR_ROOT / "sandbox_output" / self.batch_id

        if source_library_root is not None:
            self.source_library_root = source_library_root
        elif mode == "sandbox":
            self.source_library_root = SOURCE_COLLECTOR_ROOT / "sandbox_library"
        else:
            self.source_library_root = SOURCE_COLLECTOR_ROOT / "output"

        if self.mode != 'direct':
            self.target_dir = self.source_library_root / self.batch_id

        if csv_paths is not None:
            self.csv_paths = csv_paths
        elif mode == "sandbox":
            sandbox_csv = SOURCE_COLLECTOR_ROOT / "sandbox_output" / f"sandbox_전체_소스_목록_{self.batch_id}.csv"
            if DEFAULT_CSV_KO.exists():
                sandbox_csv.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(DEFAULT_CSV_KO, sandbox_csv)
            self.csv_paths = [sandbox_csv]
        else:
            self.csv_paths = [
                DEFAULT_CSV_KO,
                DEFAULT_CSV_EN,
                SOURCE_COLLECTOR_ROOT / "전체_소스_목록.csv",
                SOURCE_COLLECTOR_ROOT / "all_sources_summary.csv",
            ]

        self.deduplicator = Deduplicator()
        self.generator = SourceGenerator(
            deduplicator=self.deduplicator,
            model=self.model,
            timeout=self.timeout,
            mock=self.mock,
        )
        self.judge = HeadlessJudge(
            model=self.model,
            min_score=self.min_score,
            timeout=self.timeout,
            mock=self.mock,
        )
        self.writer = SourceWriter(
            target_dir=self.target_dir,
            source_library_root=self.source_library_root,
        )
        self.ledger = LedgerManager(
            csv_paths=self.csv_paths,
            source_library_root=self.source_library_root,
        )

    def prepare_environment(self) -> Dict[str, Any]:
        """Step 1: Prepare the target directory and preserve pending inputs."""
        archived_count = 0
        # Existing pending inputs remain queued; collector never archives them.
        self.target_dir.mkdir(parents=True, exist_ok=True)

        return {
            "mode": self.mode,
            "target_dir": str(self.target_dir),
            "archived_files": archived_count,
            "dedup_stats": self.deduplicator.stats(),
        }

    def run(self) -> Dict[str, Any]:
        """Execute full 3-step pipeline: Prep -> Generate & Prescreen -> Judge & Validate -> Ledger."""
        t0 = time.time()
        prep_info = self.prepare_environment()
        print(f"=== [Source Collector Harness] Batch: {self.batch_id} | Mode: {self.mode} ===")
        print(f"[Step 1] Target: {self.target_dir} | Dedup DB: {prep_info['dedup_stats']}")

        # Step 2A: Generate & Pre-screen candidates per category (Parallelized)
        all_candidates: List[CandidateSource] = []
        print(f"[Step 2A] Generating & pre-screening {self.count_per_category} candidate(s) across {len(self.categories)} categories (max_workers={self.max_workers})...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as gen_executor:
            gen_futures = {
                gen_executor.submit(
                    self.generator.generate_for_category,
                    cat,
                    self.count_per_category,
                    self.batch_id,
                    1,
                ): cat
                for cat in self.categories
            }
            for fut in concurrent.futures.as_completed(gen_futures):
                cat = gen_futures[fut]
                cat_name = CATEGORIES[cat]["name"]
                cands = fut.result()
                print(f"  -> [{cat} {cat_name}] {len(cands)}/{self.count_per_category} unique candidate(s) passed pre-screening.")
                all_candidates.extend(cands)

        all_candidates.sort(key=lambda x: x.id)

        # Step 2B: Parallel Headless LLM-as-a-Judge & Auto Re-elevation
        print(f"[Step 2B] Evaluating {len(all_candidates)} candidate(s) with {self.model} (max_workers={self.max_workers})...")
        accepted: List[CandidateSource] = []
        validation_failures: List[Dict[str, Any]] = []

        def _evaluate_batch(candidates_batch: List[CandidateSource]) -> None:
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                future_map = {
                    executor.submit(self.judge.evaluate_and_ensure, c): c
                    for c in candidates_batch
                }
                for future in concurrent.futures.as_completed(future_map):
                    try:
                        evaluated_cand = future.result()
                        require_safe_candidate(evaluated_cand)
                    except PrivacyViolation as exc:
                        validation_failures.append({"privacy": exc.codes})
                        print(f"  [PRIVACY BLOCKED] {exc}")
                        continue
                    if evaluated_cand.score >= self.min_score:
                        md_path = self.writer.save(evaluated_cand)
                        is_valid, val_info = self.writer.validate_file(md_path)
                        if is_valid:
                            accepted.append(evaluated_cand)
                            print(f"  [ACCEPTED] {evaluated_cand.id} ({evaluated_cand.category}) | Score: {evaluated_cand.score}/10 | Valid: True | {evaluated_cand.title[:35]}")
                        else:
                            validation_failures.append({"id": evaluated_cand.id, "validation": val_info})
                            print(f"  [INVALID]  {evaluated_cand.id} failed schema validation: {val_info}")
                    else:
                        print(f"  [DROPPED]  {evaluated_cand.id} scored {evaluated_cand.score} < {self.min_score}")

        _evaluate_batch(all_candidates)

        # Step 2C: Automatic Shortfall Refill if any category fell short
        refill_round = 0
        while refill_round < 2:
            refill_round += 1
            refill_candidates: List[CandidateSource] = []
            for cat in self.categories:
                cat_accepted = [c for c in accepted if c.category == cat]
                shortfall = self.count_per_category - len(cat_accepted)
                if shortfall > 0:
                    start_seq = self.count_per_category + (refill_round * 10) + 1
                    more = self.generator.generate_for_category(
                        category=cat,
                        count=shortfall,
                        batch_id=self.batch_id,
                        start_index=start_seq,
                    )
                    refill_candidates.extend(more)
            if not refill_candidates:
                break
            print(f"[Step 2C] Refill round {refill_round}: evaluating {len(refill_candidates)} replacement candidate(s)...")
            _evaluate_batch(refill_candidates)

        accepted.sort(key=lambda x: x.id)

        # Step 3: Update CSV Ledgers & Collection Log
        appended_count = self.ledger.append_to_csvs(accepted)
        log_path = self.ledger.write_collection_log(
            batch_id=self.batch_id,
            accepted=accepted,
            target_dir=self.target_dir,
            mode=self.mode,
        )

        elapsed = round(time.time() - t0, 2)
        summary = {
            "batch_id": self.batch_id,
            "mode": self.mode,
            "target_dir": str(self.target_dir),
            "categories_requested": self.categories,
            "count_per_category": self.count_per_category,
            "total_candidates": len(all_candidates),
            "total_accepted": len(accepted),
            "validation_failures": len(validation_failures),
            "privacy_rejections": self.generator.privacy_rejections + sum("privacy" in f for f in validation_failures),
            "csv_rows_appended": appended_count,
            "collection_log": str(log_path),
            "elapsed_sec": elapsed,
            "accepted_items": [
                {
                    "id": c.id,
                    "category": c.category,
                    "title": c.title,
                    "score": c.score,
                    "reason": c.eval_reason,
                    "chars": len(c.text),
                    "url": c.url,
                }
                for c in accepted
            ],
        }
        print(f"=== [Completed in {elapsed}s] Accepted: {len(accepted)}/{len(all_candidates)} | Log: {log_path} ===")
        return summary
