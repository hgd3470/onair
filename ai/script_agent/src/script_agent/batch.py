from .paths import output_directory, complete_job, source_reference
"""Gemini Batch API client and high-throughput batch queue execution."""
import copy
import hashlib
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import httpx
from pydantic import BaseModel

from .errors import safe_code
from .io import atomic_json, now, queue_lock, read_source
from .models import (Draft, Handoff, Material, Review,
                     RevisionResult, category_response_schema)
from .pipeline import Pipeline
from .providers import Completion, ProviderError
from .rights import resolve_rights
from .fact_source import fact_metadata
from .news_opinions import apply_news_attribution
from .publication import apply_team_copyright
from .use_assessment import apply_use_assessment
from .validation import align_material_quotes, checks, estimated_duration, validate_material


def clean_gemini_schema(schema: dict) -> dict:
    """Inline all $defs / $ref and strip OpenAPI-incompatible fields for Gemini Batch API."""
    defs = schema.get("$defs", {})

    def resolve(node, parent_key=None):
        if isinstance(node, dict):
            if "$ref" in node:
                ref_key = node["$ref"].split("/")[-1]
                if ref_key in defs:
                    resolved = resolve(defs[ref_key], parent_key)
                    extra = {k: resolve(v, k) for k, v in node.items() if k != "$ref"}
                    return {**resolved, **extra}
            res = {}
            for k, v in node.items():
                if k in {"$defs", "additionalProperties"}:
                    continue
                if k == "title" and isinstance(v, str) and parent_key != "properties":
                    continue
                res[k] = resolve(v, k)
            return res
        elif isinstance(node, list):
            return [resolve(v, parent_key) for v in node]
        return node

    return resolve(schema)


def normalize_parsed_dict(d: dict, schema: type[BaseModel]) -> dict:
    """Defensive normalizer to handle minor schema key variations or flat arrays from LLM."""
    if not isinstance(d, dict):
        return d
    allowed = set(schema.model_fields.keys())
    cleaned = {k: v for k, v in d.items() if k in allowed}
    for field_name, field_info in schema.model_fields.items():
        if field_name not in cleaned and field_info.default is not ...:
            cleaned[field_name] = field_info.default

    raw_facts = cleaned.get("facts", [])
    if isinstance(raw_facts, list) and raw_facts and isinstance(raw_facts[0], str):
        facts = []
        current: dict[str, Any] = {}
        i = 0
        while i < len(raw_facts):
            token = raw_facts[i]
            if token == "id" and i + 1 < len(raw_facts):
                if current.get("id"):
                    facts.append(current)
                current = {"id": raw_facts[i + 1]}
                i += 2
            elif token.startswith("f") and token[1:].isdigit():
                if current.get("id"):
                    facts.append(current)
                current = {"id": token}
                i += 1
            elif token in {"quote", "text", "conditions", "kind"} and i + 1 < len(raw_facts):
                val = raw_facts[i + 1]
                if token == "conditions":
                    current["conditions"] = [val] if isinstance(val, str) else list(val)
                else:
                    current[token] = val
                i += 2
            elif token in {"source_statement", "opinion", "fiction"}:
                current["kind"] = token
                if i + 1 < len(raw_facts) and str(raw_facts[i + 1]).lower() in {"true", "false"}:
                    i += 2
                else:
                    i += 1
            else:
                i += 1
        if current.get("id"):
            facts.append(current)

        for f in facts:
            f.setdefault("text", "")
            f.setdefault("quote", "")
            f.setdefault("kind", "source_statement")
            if "conditions" not in f:
                f["conditions"] = []
            elif isinstance(f["conditions"], str):
                f["conditions"] = [f["conditions"]]
        cleaned["facts"] = facts

    return cleaned


class GeminiBatchClient:
    """Client for Gemini Batch API (https://generativelanguage.googleapis.com/v1beta)."""

    def __init__(self, key: str = "", timeout: float = 60.0, auth_helper: Any = None):
        self.key = key
        self.timeout = timeout
        self.auth_helper = auth_helper
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    def _headers(self, with_content_type: bool = False) -> dict[str, str]:
        if self.auth_helper:
            h = {"Authorization": f"Bearer {self.auth_helper.get_token()}"}
        else:
            h = {"x-goog-api-key": self.key}
        if with_content_type:
            h["Content-Type"] = "application/json"
        return h

    def create_batch(self, model: str, requests: list[dict], display_name: str = "batch-job") -> str:
        """Create an inline batch generation content job."""
        url = f"{self.base_url}/models/{model}:batchGenerateContent"
        headers = self._headers(with_content_type=True)
        body = {
            "batch": {
                "displayName": display_name,
                "inputConfig": {
                    "requests": {
                        "requests": requests
                    }
                }
            }
        }
        # A POST may have succeeded even if the reply was lost. Never retry it
        # automatically: the durable submission intent requires reconciliation.
        try:
            response = httpx.post(url, headers=headers, json=body, timeout=self.timeout)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderError("gemini", model, "transport_or_timeout",
                                diagnostics={"transport_failure": type(exc).__name__}) from None
        if response.is_error:
            raise ProviderError("gemini", model, "api_error", response.status_code)

        data = response.json()
        batch_name = data.get("name")
        if not batch_name:
            raise ProviderError("gemini", model, "batch_create_missing_name")
        return batch_name

    def get_batch(self, batch_name: str) -> dict:
        """Get the current batch status and metadata."""
        url = f"{self.base_url}/{batch_name}"
        headers = self._headers()
        response = httpx.get(url, headers=headers, timeout=self.timeout)
        if response.is_error:
            raise ProviderError("gemini", "batch", "api_error", response.status_code)
        return response.json()

    def download_file(self, file_name: str) -> str:
        """Download batch results from File API if response is stored in a file."""
        url = f"https://generativelanguage.googleapis.com/download/v1beta/{file_name}:download?alt=media"
        headers = self._headers()
        response = httpx.get(url, headers=headers, timeout=self.timeout)
        if response.is_error:
            raise ProviderError("gemini", "batch", "file_download_failed", response.status_code)
        return response.text

    def poll_batch(self, batch_name: str, interval_sec: float = 15.0,
                   timeout_sec: float | None = None, on_poll: Callable[[dict], None] | None = None) -> dict:
        """Poll batch job until a terminal state is reached."""
        timeout_sec = getattr(self, 'wait_timeout_sec', 86400) if timeout_sec is None else timeout_sec
        terminal_states = {
            "BATCH_STATE_SUCCEEDED", "JOB_STATE_SUCCEEDED",
            "BATCH_STATE_FAILED", "JOB_STATE_FAILED",
            "BATCH_STATE_CANCELLED", "JOB_STATE_CANCELLED",
            "BATCH_STATE_EXPIRED", "JOB_STATE_EXPIRED"
        }
        start = time.monotonic()
        while True:
            data = self.get_batch(batch_name)
            state = data.get("state") or data.get("metadata", {}).get("state")
            if on_poll:
                on_poll(data)
            if state in terminal_states or data.get("done") is True:
                return data
            if time.monotonic() - start > timeout_sec:
                raise ProviderError("gemini", "batch", "batch_polling_timeout")
            time.sleep(max(1.0, interval_sec))

    def extract_items(self, batch_data: dict) -> list[dict]:
        """Extract item responses or errors mapped by metadata key."""
        inlined = (
            batch_data.get("response", {}).get("inlinedResponses")
            or batch_data.get("dest", {}).get("inlinedResponses")
            or []
        )
        if isinstance(inlined, dict):
            inlined = inlined.get('inlinedResponses', [])
        if inlined:
            return inlined

        file_name = (
            batch_data.get("dest", {}).get("fileName")
            or batch_data.get("response", {}).get("responsesFile")
        )
        if file_name:
            content = self.download_file(file_name)
            items = []
            for line in content.splitlines():
                if line.strip():
                    items.append(json.loads(line))
            return items

        return []


def parse_item_response(item_data: dict, schema: type[BaseModel], model: str) -> Completion:
    """Parse a single item response from a batch job into a typed Completion."""
    if "error" in item_data:
        err = item_data["error"]
        raise ProviderError("gemini", model, "batch_item_error", diagnostics=err)

    resp = item_data.get("response", {})
    candidates = resp.get("candidates") or [{}]
    candidate = candidates[0]
    finish_reason = candidate.get("finishReason")
    if finish_reason != "STOP":
        allowed = {
            "MAX_TOKENS", "SAFETY", "RECITATION", "OTHER", "BLOCKLIST",
            "PROHIBITED_CONTENT", "SPII", "MALFORMED_FUNCTION_CALL",
            "LANGUAGE", "FINISH_REASON_UNSPECIFIED"
        }
        diagnostics = {"finish_reason": finish_reason if finish_reason in allowed else "UNKNOWN"}
        usage = resp.get("usageMetadata", {})
        diagnostics["token_counts"] = {
            k: v for k, v in usage.items()
            if k in {"promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "totalTokenCount"}
            and type(v) is int and v >= 0
        }
        raise ProviderError("gemini", model, "incomplete_or_blocked", diagnostics=diagnostics)

    raw = "".join(
        p.get("text", "") for p in candidate.get("content", {}).get("parts", [])
        if not p.get("thought")
    )
    usage = resp.get("usageMetadata", {})
    returned_model = resp.get("modelVersion", model)

    try:
        parsed = schema.model_validate_json(raw)
    except Exception:
        try:
            d = json.loads(raw)
            norm = normalize_parsed_dict(d, schema)
            parsed = schema.model_validate(norm)
        except Exception:
            raise ProviderError("gemini", model, "invalid_structured_output") from None

    return Completion(parsed, usage, 0.0, returned_model)


def _format_event(stage: str, model_config: dict, completion: Completion, system: str, prompt_version: str) -> dict:
    return {
        'stage': stage,
        'model': model_config['model'],
        'response_model': completion.response_model,
        'effort': model_config['effort'],
        'prompt_version': prompt_version,
        'prompt_sha256': hashlib.sha256(system.encode()).hexdigest(),
        'usage': completion.usage,
        'elapsed_sec': completion.elapsed_sec,
        'at': now()
    }


def process_batch_queue(root: Path, pipeline: Pipeline, limit: int = 100) -> list[dict]:
    """Execute queue using Gemini Batch API for analyze/generate/revise and real-time parallel GPT for review."""
    if limit < 1:
        raise ValueError('limit_must_be_positive')

    for folder in ('input', 'processing', 'failed', 'output'):
        (root / folder).mkdir(exist_ok=True)

    config = pipeline.config
    batch_cfg = config.get('batch', {})
    batch_size = int(batch_cfg.get('batch_size', 100))
    poll_interval = float(batch_cfg.get('poll_interval_sec', 15))
    gpt_workers = int(batch_cfg.get('gpt_workers', 6))
    if batch_size < 1 or gpt_workers < 1 or poll_interval <= 0:
        raise ValueError('invalid_batch_settings')
    gen_config = config['models']['generator']
    gen_provider = pipeline.providers['generator']
    if gen_config['provider'] != 'gemini':
        raise ProviderError('gemini', gen_config['model'], 'batch_requires_gemini_provider')
    gemini_key = gen_provider.keys.get('gemini_api_key')
    if not gemini_key:
        raise ProviderError('gemini', gen_config['model'], 'missing_key')

    # Explicit Gemini API-key batches must not switch to host service accounts.
    batch_client = GeminiBatchClient(gemini_key, timeout=float(config['pipeline'].get('timeout_sec', 300)))
    batch_client.wait_timeout_sec = float(batch_cfg.get('wait_timeout_sec', 86400))
    if batch_client.wait_timeout_sec < 0:
        raise ValueError('invalid_batch_settings')
    results: list[dict] = []

    with queue_lock(root):
        # 1. Claim pending or resuming jobs up to limit
        jobs = sorted((root / 'processing').glob('*/job.json'))
        for path in sorted((root / 'input').iterdir()):
            if len(jobs) >= limit:
                break
            if path.suffix.lower() not in {'.txt', '.md'} or path.is_symlink():
                continue
            job_dir = root / 'processing' / uuid.uuid4().hex
            job_dir.mkdir()
            state = {'filename': path.name, 'claimed_at': now(), 'status': 'claiming'}
            atomic_json(job_dir / 'job.json', state)
            path.replace(job_dir / path.name)
            sidecar = path.with_suffix('.meta.json')
            if sidecar.exists():
                sidecar.replace(job_dir / sidecar.name)
            state['status'] = 'claimed'
            atomic_json(job_dir / 'job.json', state)
            jobs.append(job_dir / 'job.json')

        if not jobs:
            return []

        active_jobs: list[dict[str, Any]] = []

        # 2. Inspect jobs, handle duplicates, recover claiming interruptions
        for job_path in jobs[:limit]:
            job_dir = job_path.parent
            state = json.loads(job_path.read_text(encoding='utf-8'))
            source_file = job_dir / state['filename']
            if state['status'] == 'claiming':
                original = root / 'input' / state['filename']
                if not source_file.exists() and original.exists():
                    original.replace(source_file)
                sidecar = original.with_suffix('.meta.json')
                if sidecar.exists() and not source_file.with_suffix('.meta.json').exists():
                    sidecar.replace(source_file.with_suffix('.meta.json'))

            try:
                text, meta, digest = read_source(source_file, config['pipeline']['max_source_chars'])
                content_id = 'S2-' + digest[:16]
                output_path = output_directory(root, meta.source_id) / (content_id + '.json')
                if output_path.exists():
                    out = json.loads(output_path.read_text(encoding='utf-8'))
                    if out['input_sha256'] != digest:
                        raise ValueError('content_id_collision')
                    state.update(status='completed', completed_at=now(), content_id=out['content_id'],
                                 output_status=out['status'], duplicate=True)
                    atomic_json(job_path, state)
                    complete_job(root, job_dir, meta.source_id or ('src-' + digest[:16]))
                    results.append(state)
                    continue

                source_id = meta.source_id or ('src-' + digest[:16])
                source_path = source_reference(root, output_directory(root, source_id), source_id, digest)

                log_dir = root / 'logs' / content_id
                log_dir.mkdir(parents=True, exist_ok=True)

                _check_batch_contract(log_dir, pipeline)
                active_jobs.append({
                    'job_id': job_dir.name,
                    'job_dir': job_dir,
                    'job_path': job_path,
                    'state': state,
                    'text': text,
                    'meta': meta,
                    'digest': digest,
                    'content_id': content_id,
                    'source_id': source_id,
                    'source_path': source_path,
                    'log_dir': log_dir,
                    'events': [],
                    'prompt_version': config['pipeline']['prompt_version'],
                    'base': {
                        'source_text': text,
                        'metadata': meta.model_dump(),
                        'target_sec': meta.target_sec,
                        'target_chars': round(meta.target_sec * config['pipeline']['chars_per_sec'])
                    }
                })
            except Exception as exc:
                state.update(status='failed', failed_at=now(), error_type=type(exc).__name__, code=safe_code(exc))
                atomic_json(job_path, state)
                job_dir.replace(root / 'failed' / job_dir.name)
                results.append(state)

        if not active_jobs:
            return results

        # -------------------------------------------------------------
        # Phase 1: Batch Analyze (Gemini Batch API)
        # -------------------------------------------------------------
        for job in active_jobs:
            job['analyze_schema'] = category_response_schema(Material, job['meta'].category)
            job['analyze_system'] = pipeline.prompt('analyze', job['meta'].production_type)
        _run_gemini_batch_stage(batch_client, gen_config, active_jobs,
                                prompt_stage='analyze', batch_size=batch_size,
                                poll_interval=poll_interval)

        # Post-process analyze results (quotes alignment, validation, rights resolution)
        failed_during_analyze: list[dict[str, Any]] = []
        for job in list(active_jobs):
            if 'material' not in job:
                failed_during_analyze.append(job)
                continue
            try:
                material, alignments = align_material_quotes(job['material'], job['text'])
                if alignments:
                    atomic_json(job['log_dir'] / 'analysis-quote-alignment.json', alignments)
                validate_material(material, job['text'], job['meta'])
                resolved, rights_notes, rights = resolve_rights(
                    job['meta'].model_copy(update={'production_type': material.production_type})
                )
                job['material'] = material
                job['resolved'] = resolved
                job['rights_notes'] = rights_notes
                job['rights'] = rights
                job['base']['metadata'] = resolved.model_dump()
                atomic_json(job['log_dir'] / 'material.json', material.model_dump())
                job['base']['material'] = material.model_dump()
            except Exception as exc:
                job['error'] = exc
                failed_during_analyze.append(job)

        for job in failed_during_analyze:
            if job in active_jobs:
                active_jobs.remove(job)
            _record_job_failure(job, results, root)

        if not active_jobs:
            return results

        # -------------------------------------------------------------
        # Phase 2: Batch Generate (Gemini Batch API)
        # -------------------------------------------------------------
        for job in active_jobs:
            job['generate_schema'] = Draft
            job['generate_system'] = pipeline.prompt('generate', job['material'].production_type)
        _run_gemini_batch_stage(batch_client, gen_config, active_jobs,
                                prompt_stage='generate', batch_size=batch_size,
                                poll_interval=poll_interval)

        # Check for any failures during generate
        failed_during_generate: list[dict[str, Any]] = []
        for job in list(active_jobs):
            if 'draft' not in job:
                failed_during_generate.append(job)
                continue
        for job in failed_during_generate:
            if job in active_jobs:
                active_jobs.remove(job)
            _record_job_failure(job, results, root)

        if not active_jobs:
            return results

        # -------------------------------------------------------------
        # Phase 3 & 4: Review Loop (Real-time Parallel GPT & Batch Revise)
        # -------------------------------------------------------------
        cps = config['pipeline']['chars_per_sec']
        max_revisions = config['pipeline']['max_revisions']
        for job in active_jobs:
            job['revision'] = 0
            job['history'] = []
            job['current_review'] = None

        # Replay validated stage checkpoints from the beginning. This rebuilds
        # material, draft, history and counters together, including a revision
        # received immediately before interruption. Cached calls make no API calls.
        active_review_pool = list(active_jobs)

        while active_review_pool:
            # Run GPT review in parallel using ThreadPoolExecutor
            print(f"[Batch Mode] Running real-time GPT review on {len(active_review_pool)} items with {gpt_workers} parallel workers...", flush=True)
            _run_parallel_gpt_review(pipeline, active_review_pool, gpt_workers=gpt_workers, cps=cps)

            # Check which jobs require revision vs which jobs are finished
            revise_needed: list[dict[str, Any]] = []
            for job in list(active_review_pool):
                rev = job['revision']
                current_review = job.get('current_review')
                if not current_review:
                    # Failed during review; remove from active review pool
                    active_review_pool.remove(job)
                    active_jobs.remove(job)
                    _record_job_failure(job, results, root)
                elif current_review.verdict != 'revise' or rev >= max_revisions:
                    active_review_pool.remove(job)
                else:
                    revise_needed.append(job)

            if not revise_needed:
                break

            # Batch Revise
            print(f"[Batch Mode] Submitting {len(revise_needed)} revision requests to Gemini Batch API...", flush=True)
            for job in revise_needed:
                system = pipeline.prompt('revise', job['material'].production_type)
                rev_schema = category_response_schema(RevisionResult, job['material'].category)
                payload = {**job['base'], 'draft': job['draft'].model_dump(),
                           'feedback': job['current_review'].model_dump(), 'previous_reviews': job['history']}
                job['revise_payload'] = payload
                job['revise_system'] = system
                job['revise_schema'] = rev_schema

            _run_gemini_batch_stage(batch_client, gen_config, revise_needed,
                                    prompt_stage='revise', batch_size=batch_size,
                                    poll_interval=poll_interval)

            # Validate and apply each response independently. Failed jobs never
            # enter final packaging, and stale revision results cannot be reused.
            for job in revise_needed:
                try:
                    if 'revised_result' not in job:
                        raise job.get('error') or ValueError('batch_result_missing')
                    changed = job.pop('revised_result')
                    previous = job['material']
                    material = changed.material if changed.material is not None else previous
                    material, alignments = align_material_quotes(material, job['text'])
                    if alignments:
                        atomic_json(job['log_dir'] / f"{len(job['events']):02d}-revise-quote-alignment.json", alignments)
                    validate_material(material, job['text'], job['meta'])
                    if (material.production_type, material.category) != (previous.production_type, previous.category):
                        raise ValueError('revision_cannot_change_route')
                    if not {f.id for f in previous.facts}.issubset({f.id for f in material.facts}):
                        raise ValueError('revision_cannot_remove_fact_ids')
                    job['material'] = material
                    job['draft'] = changed.draft
                    job['base']['material'] = material.model_dump()
                    job['revision'] += 1
                    job['current_review'] = None
                except Exception as exc:
                    job['error'] = exc
                    active_review_pool.remove(job)
                    active_jobs.remove(job)
                    _record_job_failure(job, results, root)

        # -------------------------------------------------------------
        # Phase 5: Handoff Packaging and Archiving
        # -------------------------------------------------------------
        for job in active_jobs:
            try:
                if not job.get('draft') or not job.get('material') or not job.get('current_review'):
                    _record_job_failure(job, results, root)
                    continue

                draft = job['draft']
                material = job['material']
                review = job['current_review']
                revision = job['revision']
                resolved = job['resolved']
                rights = job['rights']
                rights_notes = job['rights_notes']
                meta = job['meta']
                text = job['text']

                status = 'ready_for_downstream_review' if review.verdict == 'pass' else 'hold'
                if rights in {'restricted', 'unverified'}:
                    status = 'hold'

                source_entry = {
                    'source_id': job['source_id'], 'source_url': meta.source_url, 'author': meta.author,
                    'license': resolved.license.model_dump(), 'attribution': resolved.attribution,
                    'source_kind': resolved.source_kind,
                    'original_sources': [s.model_dump() for s in resolved.original_sources],
                    'text_path': str(job['source_path']), 'text': text, 'info_as_of': meta.info_as_of,
                    'collected_at': meta.collected_at, 'edition': meta.edition,
                    'sha256': hashlib.sha256(text.encode()).hexdigest()
                }

                source_entry = fact_metadata(source_entry)
                output = {
                    'schema_version': '1.0.0', 'content_id': job['content_id'], 'created_at': now(),
                    'title': draft.title, 'category': material.category, 'format': draft.format,
                    'production_type': material.production_type, 'tags': draft.tags,
                    'version': f'1.0.{revision}', 'target_sec': meta.target_sec,
                    'script': draft.script, 'sources': [source_entry], 'material': material.model_dump(),
                    'claims': [c.model_dump() for c in draft.claims],
                    'adaptation_note': draft.adaptation_note.model_dump(),
                    'license': resolved.license.kind, 'attribution': resolved.attribution,
                    'source_url': meta.source_url,
                    'generator': {
                        'model': gen_config['model'],
                        'effort': gen_config['effort'],
                        'prompt_version': config['pipeline']['prompt_version'],
                        'revision': revision,
                        'feedback_revision': 0,
                        'self_check_model': config['models']['reviewer']['model'],
                        'revision_model': gen_config['model'],
                        'self_check_effort': config['models']['reviewer']['effort'],
                        'calls': job['events']
                    },
                    'status': status, 'rights_status': rights, 'rights_notes': rights_notes,
                    'input_sha256': job['digest'],
                    'self_review': job['history'], 'editorial_brief': [],
                    'unresolved': draft.unresolved + review.remaining,
                    'duration': {
                        'estimated_sec': estimated_duration(draft, cps, config['pipeline'].get('character_count_mode', 'non_whitespace')),
                        'measured_sec': None,
                        'method': config['pipeline'].get('character_count_mode', 'non_whitespace') + '_chars / chars_per_sec (user-calibrated estimate; not measured audio)',
                        'chars_per_sec': cps
                    },
                    'fixture': meta.fixture, 'input_metadata': meta.model_dump(),
                    'downstream_approval': 'pending', 'tts_checked': False
                }

                apply_use_assessment(apply_team_copyright(apply_news_attribution(output)))
                Handoff.model_validate(output)
                atomic_json(output_directory(root, job['source_id']) / f"{job['content_id']}.json", output)
                job['state'].update(status='completed', completed_at=now(), content_id=job['content_id'],
                                    output_status=output['status'], duplicate=False)
                atomic_json(job['job_path'], job['state'])
                if job['job_dir'].exists():
                    complete_job(root, job['job_dir'], job['source_id'])
                results.append(job['state'])
            except Exception as exc:
                job['error'] = exc
                _record_job_failure(job, results, root)

    return results


def _check_batch_contract(log_dir: Path, pipeline: Pipeline):
    """Reject legacy or incompatible logs before replay or network submission."""
    contract = {
        'batch_checkpoint_version': 2,
        'pipeline': pipeline.config['pipeline'],
        'models': pipeline.config['models'],
        'prompts': {stage: pipeline.prompt(stage) for stage in ('analyze', 'generate', 'review', 'revise')},
        'schemas': {model.__name__: model.model_json_schema()
                    for model in (Material, Draft, Review, RevisionResult)},
    }
    path = log_dir / 'batch-contract.json'
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) != contract:
            raise ValueError('checkpoint_config_changed_use_new_destination')
    elif any(log_dir.glob('*.json')):
        raise ValueError('checkpoint_config_changed_use_new_destination')
    else:
        atomic_json(path, contract)


def _request_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _run_gemini_batch_stage(batch_client: GeminiBatchClient, gen_config: dict,
                            jobs: list[dict[str, Any]], prompt_stage: str,
                            batch_size: int, poll_interval: float):
    """Replay cached results or resume durable remote batches, per job and stage."""
    field = {'analyze': 'material', 'generate': 'draft', 'revise': 'revised_result'}[prompt_stage]
    fresh = []
    pending = {}
    # Recover references even if interruption occurred halfway through writing
    # per-job pointers, before the shared manifest's POST was submitted.
    known_requests = {}
    if jobs:
        for path in (jobs[0]['log_dir'].parent / 'batch-jobs').glob('*.json'):
            saved = json.loads(path.read_text(encoding='utf-8'))
            for req in saved['requests']:
                known_requests[(_request_hash(req), saved['model'])] = path
    for job in jobs:
        job.pop(field, None)
        job.pop('error', None)
        schema = job[f'{prompt_stage}_schema']
        system = job[f'{prompt_stage}_system']
        payload = job['revise_payload'] if prompt_stage == 'revise' else job['base']
        prefix = job['log_dir'] / f"{len(job['events']) + 1:02d}-{prompt_stage}"
        request_path = Path(str(prefix) + '-request.json')
        result_path = Path(str(prefix) + '-result.json')
        ref_path = Path(str(prefix) + '-batch.json')
        request = {'system': system, 'payload': payload, 'settings': gen_config,
                   'response_schema': schema.model_json_schema()}
        try:
            if request_path.exists():
                if json.loads(request_path.read_text(encoding='utf-8')) != request:
                    raise ValueError('checkpoint_config_changed_use_new_destination')
            elif result_path.exists() or ref_path.exists():
                raise ValueError('checkpoint_config_changed_use_new_destination')
            else:
                atomic_json(request_path, request)
            if result_path.exists():
                cached = json.loads(result_path.read_text(encoding='utf-8'))
                job[field] = schema.model_validate(cached['result'])
                job['events'].append(cached['event'])
                continue
            gemini_req = {
                'request': {
                    'systemInstruction': {'parts': [{'text': system}]},
                    'contents': [{'role': 'user', 'parts': [{'text': json.dumps(payload, ensure_ascii=False)}]}],
                    'generationConfig': {
                        'thinkingConfig': {'thinkingLevel': gen_config['effort']},
                        'maxOutputTokens': gen_config['max_output_tokens'],
                        'responseMimeType': 'application/json',
                        'responseSchema': clean_gemini_schema(schema.model_json_schema()),
                    },
                },
                'metadata': {'key': job['job_id']},
            }
            entry = (job, gemini_req, result_path, ref_path)
            existing = known_requests.get((_request_hash(gemini_req), gen_config['model']))
            if not ref_path.exists() and existing:
                atomic_json(ref_path, {'manifest': existing.name})
            if ref_path.exists():
                ref = json.loads(ref_path.read_text(encoding='utf-8'))
                manifest_path = job['log_dir'].parent / 'batch-jobs' / ref['manifest']
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                if gemini_req not in manifest['requests'] or manifest['model'] != gen_config['model']:
                    raise ValueError('checkpoint_config_changed_use_new_destination')
                pending.setdefault(manifest_path, []).append(entry)
            else:
                fresh.append(entry)
        except Exception as exc:
            job['error'] = exc

    for offset in range(0, len(fresh), batch_size):
        entries = fresh[offset:offset + batch_size]
        manifest_path = entries[0][0]['log_dir'].parent / 'batch-jobs' / (uuid.uuid4().hex + '.json')
        manifest = {'model': gen_config['model'], 'requests': [entry[1] for entry in entries],
                    'state': 'prepared', 'display_name': f"{prompt_stage}-{manifest_path.stem}"}
        manifest['request_sha256'] = _request_hash(manifest['requests'])
        atomic_json(manifest_path, manifest)
        for job, req, result_path, ref_path in entries:
            atomic_json(ref_path, {'manifest': manifest_path.name})
        pending[manifest_path] = entries

    for manifest_path, entries in pending.items():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('request_sha256') != _request_hash(manifest['requests']):
            raise ValueError('checkpoint_config_changed_use_new_destination')
        if not manifest.get('batch_name'):
            if manifest['state'] != 'prepared':
                # The server may have accepted a POST whose response was lost.
                raise ProviderError('gemini', gen_config['model'], 'batch_submission_unknown')
            manifest['state'] = 'submitting'
            atomic_json(manifest_path, manifest)
            batch_name = batch_client.create_batch(manifest['model'], manifest['requests'], manifest['display_name'])
            manifest.update(batch_name=batch_name, state='submitted')
            atomic_json(manifest_path, manifest)
            print(f"[Gemini Batch] Submitted {prompt_stage}: {batch_name}", flush=True)

    # Submit all chunks before waiting, preserving batch throughput.
    for manifest_path, entries in pending.items():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if 'items' not in manifest:
            # Transport/poll failures leave jobs in processing, with the remote ID.
            completed = batch_client.poll_batch(manifest['batch_name'], interval_sec=poll_interval)
            terminal_state = completed.get('state') or completed.get('metadata', {}).get('state')
            succeeded = terminal_state in {'JOB_STATE_SUCCEEDED', 'BATCH_STATE_SUCCEEDED'} and not completed.get('error')
            items = batch_client.extract_items(completed) if succeeded else []
            manifest.update(state='completed', items=items, terminal_state=terminal_state)
            atomic_json(manifest_path, manifest)
        if manifest.get('terminal_state') not in {'JOB_STATE_SUCCEEDED', 'BATCH_STATE_SUCCEEDED'}:
            for job, *_ in entries:
                job['error'] = ProviderError('gemini', gen_config['model'], 'batch_job_failed')
            continue
        by_key = {}
        for item in manifest['items']:
            key = item.get('metadata', {}).get('key') or item.get('key')
            by_key.setdefault(key, []).append(item)
        for job, req, result_path, ref_path in entries:
            try:
                matches = by_key.get(job['job_id'], [])
                if len(matches) != 1:
                    raise ProviderError('gemini', gen_config['model'], 'batch_result_missing_or_duplicate')
                schema = job[f'{prompt_stage}_schema']
                comp = parse_item_response(matches[0], schema, gen_config['model'])
                event = _format_event(prompt_stage, gen_config, comp, job[f'{prompt_stage}_system'], job['prompt_version'])
                atomic_json(result_path, {'event': event, 'result': comp.value.model_dump()})
                job[field] = comp.value
                job['events'].append(event)
            except Exception as exc:
                job['error'] = exc


def _run_parallel_gpt_review(pipeline: Pipeline, jobs: list[dict[str, Any]],
                             gpt_workers: int, cps: float):
    """Run real-time OpenAI review in parallel across jobs using a thread pool."""
    def review_single(job):
        local_pipeline = copy.copy(pipeline)
        local_pipeline.events = job['events']
        draft = job['draft']
        material = job['material']
        meta = job['meta']
        text = job['text']
        rev = job['revision']
        history = job['history']

        draft.format = {
            '창작·각색형': '이야기형', '사실해설형': '정보형',
            '사실서사형': '이야기형', '실행안내형': '안내형',
            '해석·감상형': '감상형', '원문낭독형': '본문낭독형'
        }[material.production_type]

        deterministic = checks(draft, material, text, meta, cps, pipeline.config['pipeline'].get('character_count_mode', 'non_whitespace'), min_sec=pipeline.config['pipeline'].get('duration_min_sec'), max_sec=pipeline.config['pipeline'].get('duration_max_sec'))
        payload = {
            **job['base'], 'draft': draft.model_dump(),
            'checks': [i.model_dump() for i in deterministic],
            'revision': rev, 'previous_reviews': history
        }
        review = None
        for attempt in range(3):
            try:
                review = local_pipeline.call('review', payload, Review, job['log_dir'], material.production_type)
                break
            except Exception as exc:
                if attempt < 2 and (
                    isinstance(exc, ProviderError) and exc.code in {'incomplete_or_refused', 'transport_or_timeout', 'timeout', 'rate_limit'}
                    or isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))
                ):
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
        if deterministic:
            review.issues.extend(deterministic)
            if review.verdict == 'pass':
                review.verdict = 'revise'
        if material.production_type == '원문낭독형':
            quoted = {c.sentence for c in draft.claims if c.kind == 'quotation'}
            if any(i.sentence in quoted and i.problem in {'문장 40자 초과', '숫자·영문 한글 읽기 필요'}
                   for i in deterministic):
                review.verdict = 'needs_evidence'
                review.remaining.append('지정 원문 보존과 낭독 표기·40자 규칙 충돌: 적합한 판본/낭독 범위 확인 필요')
        if review.issues and review.verdict == 'pass':
            review.verdict = 'revise'
        if draft.unresolved and review.verdict == 'pass':
            review.verdict = 'needs_evidence'

        history.append({'revision': rev, **review.model_dump()})
        atomic_json(job['log_dir'] / f"revision-{rev}.json",
                    {'draft': draft.model_dump(), 'material': material.model_dump(),
                     'review': review.model_dump()})
        job['current_review'] = review
        return job

    with ThreadPoolExecutor(max_workers=gpt_workers) as pool:
        futures = {pool.submit(review_single, j): j for j in jobs}
        for f in as_completed(futures):
            target = futures[f]
            try:
                f.result()
            except Exception as exc:
                target['error'] = exc
                target['current_review'] = None


def _record_job_failure(job: dict[str, Any], results: list[dict], root: Path):
    exc = job.get('error')
    job_dir = job['job_dir']
    job_path = job['job_path']
    state = job['state']
    state.update(status='failed', failed_at=now(), error_type=type(exc).__name__ if exc else 'UnknownError')
    if isinstance(exc, ProviderError):
        state.update(provider=exc.provider, model=exc.model, code=exc.code,
                     http_status=exc.status, diagnostics=exc.diagnostics)
    else:
        state['code'] = safe_code(exc) if exc else 'execution_failed'
    atomic_json(job_path, state)
    if job_dir.exists():
        job_dir.replace(root / 'failed' / job_dir.name)
    results.append(state)
