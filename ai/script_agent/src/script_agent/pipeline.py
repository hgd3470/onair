from .paths import output_directory, log_directory, source_reference
import hashlib
import os
import json
import time
from datetime import datetime
from pathlib import Path

from .io import atomic_json, now
from .models import (BriefReviewResult, Draft, Handoff, Issue, Material, Review,
                     RevisionResult, category_response_schema)
from .providers import APIProvider, ProviderError, Completion
from . import batch_transport
from .rights import resolve_rights
from .fact_source import fact_metadata
from .news_opinions import apply_news_attribution
from .publication import apply_team_copyright
from .use_assessment import apply_use_assessment
from .validation import align_material_quotes, checks, estimated_duration, validate_material


def validate_editorial_brief(value):
    if value is None:
        return []
    if (not isinstance(value, list) or len(value) > 32
            or any(not isinstance(x, str) or not x.strip() or len(x) > 4000 for x in value)
            or sum(len(x) for x in value) > 20000):
        raise ValueError('invalid_editorial_brief')
    return list(value)


def editorial_issues(review, brief):
    expected = set(range(1, len(brief) + 1))
    ids = [c.requirement for c in review.editorial_checks]
    issues = []
    if set(ids) != expected or len(ids) != len(expected):
        issues.append(Issue(sentence=0, problem='사용자 피드백 확인 누락',
                            reason='검토 항목 번호가 편집 요구사항과 일치하지 않음',
                            direction='editorial_brief의 모든 항목을 빠짐없이 확인하고 대본에 반영'))
    for check in review.editorial_checks:
        if not check.satisfied and check.requirement in expected:
            issues.append(Issue(sentence=0, problem='사용자 피드백 미해결',
                                reason=check.evidence, direction=brief[check.requirement - 1]))
    return issues


class Pipeline:
    def __init__(self, root: Path, config: dict, keys: dict, providers=None):
        self.root, self.config = root, config
        self.custom_providers = providers is not None
        self.prompt_dir = root / 'prompts' / config['pipeline']['prompt_version']
        self.providers = providers or {role: APIProvider(s, keys, config['pipeline']['timeout_sec'], root=root)
                                      for role, s in config['models'].items()}
        self.events = []
        self.deadline = None

    def prompt(self, stage, production_type=None):
        common = (self.prompt_dir / 'common.txt').read_text(encoding='utf-8')
        stage_text = (self.prompt_dir / f'{stage}.txt').read_text(encoding='utf-8')
        if stage == 'revise':
            stage_text += '\n' + (self.root / 'prompts/feedback-scope.txt').read_text(encoding='utf-8')
        types = json.loads((self.prompt_dir / 'types.json').read_text(encoding='utf-8'))
        rule = types[production_type] if production_type else json.dumps(types, ensure_ascii=False)
        if self.config['pipeline'].get('character_count_mode') == 'with_spaces':
            stage_text += '\n분량 계산 계약: target_chars는 공백·문장부호 포함 NFC 글자 수다. 문장을 공백 하나로 이어 계산한다. 앞의 공백 제외 분량 규칙 대신 이 계약을 적용한다. 입력 target_sec에 맞추고 근거 없는 내용이나 반복으로 채우지 않는다.'
        if self.config['pipeline'].get('duration_max_sec') is not None:
            stage_text += f"\n분량 절대 범위: 보정 추정 {self.config['pipeline'].get('duration_min_sec', 0)}~{self.config['pipeline']['duration_max_sec']}초를 지킨다. 목표 분량의 70~130% 허용보다 이 절대 범위를 우선한다."
        return common + '\n' + stage_text + '\n유형별 규칙:\n' + rule

    def call(self, stage, payload, schema, log_dir, production_type=None):
        if self.deadline is not None:
            remaining = (self.deadline - datetime.now(self.deadline.tzinfo)).total_seconds()
            if remaining <= 0:
                raise ValueError('experiment_deadline_reached')
        if schema is Material:
            schema = category_response_schema(schema, payload.get('metadata', {}).get('category'))
        elif schema is RevisionResult:
            # An inferred route is also fixed once analysis has completed.
            category = payload['material']['category']
            schema = category_response_schema(schema, category)
        # Generation and feedback-driven revision share the configured Gemini model.
        role = 'generator' if stage in {'analyze', 'generate', 'revise'} else 'reviewer'
        settings = self.config['models'][role]
        use_batch = (not self.custom_providers and self.config['pipeline'].get('mode') == 'batch'
                     and settings['provider'] in {'gemini', 'vertex'})
        if use_batch and settings['provider'] != 'gemini':
            raise ProviderError('gemini', settings['model'], 'batch_requires_gemini_provider')
        system = self.prompt(stage, production_type)
        number = len(self.events) + 1
        # Full reproducible prompt/input and validated result. No credentials or HTTP bodies.
        request_path = log_dir / f'{number:02d}-{stage}-request.json'
        result_path = log_dir / f'{number:02d}-{stage}-result.json'
        request = {'system': system, 'payload': payload, 'settings': self.config['models'][role],
                   'response_schema': schema.model_json_schema()}
        if use_batch:
            request['execution_mode'] = 'batch'
        if request_path.exists() and json.loads(request_path.read_text(encoding='utf-8')) != request:
            raise ValueError('checkpoint_config_changed_use_new_destination')
        if request_path.exists() and result_path.exists():
            previous_request = json.loads(request_path.read_text(encoding='utf-8'))
            if previous_request != request:
                raise ValueError('checkpoint_config_changed_use_new_destination')
            previous = json.loads(result_path.read_text(encoding='utf-8'))
            self.events.append(previous['event'])
            return schema.model_validate(previous['result'])
        atomic_json(request_path, request)
        if self.deadline is not None and hasattr(self.providers[role], 'timeout'):
            self.providers[role].timeout = min(self.config['pipeline']['timeout_sec'], max(1, remaining))
        try:
            if use_batch:
                cfg = self.config.get('batch', {})
                started = time.monotonic()
                try:
                    data = batch_transport.generate(
                        batch_transport.request_body(system, payload, settings, schema),
                        model=settings['model'], key=self.providers[role].keys.get('gemini_api_key'),
                        path=log_dir / f'{number:02d}-{stage}-batch.json',
                        timeout=self.providers[role].timeout,
                        poll_interval=cfg.get('poll_interval_sec', 15),
                        wait_timeout=min(cfg.get('wait_timeout_sec', 86400), remaining)
                            if self.deadline is not None else cfg.get('wait_timeout_sec', 86400))
                    value = schema.model_validate_json(batch_transport.response_text(data))
                except batch_transport.BatchError as exc:
                    raise ProviderError('gemini', settings['model'], exc.code) from None
                except ValueError:
                    raise ProviderError('gemini', settings['model'], 'invalid_structured_output') from None
                result = Completion(value, data.get('usageMetadata', {}), time.monotonic() - started,
                                    data.get('modelVersion', settings['model']))
            else:
                result = self.providers[role].complete(system, payload, schema)
        except ProviderError as exc:
            from uuid import uuid4
            atomic_json(log_dir / f'{number:02d}-{stage}-error-{uuid4().hex}.json', {
                'stage': stage, 'code': exc.code, 'provider': exc.provider,
                'model': exc.model, 'http_status': exc.status,
                'diagnostics': exc.diagnostics, 'at': now()})
            raise
        value = schema.model_validate(result.value.model_dump())
        event = {'stage': stage, 'model': self.config['models'][role]['model'],
                 'response_model': result.response_model, 'effort': self.config['models'][role]['effort'],
                 'prompt_version': self.config['pipeline']['prompt_version'],
                 'prompt_sha256': hashlib.sha256(system.encode()).hexdigest(),
                 'usage': result.usage, 'elapsed_sec': result.elapsed_sec, 'at': now()}
        self.events.append(event)
        atomic_json(result_path, {'event': event, 'result': value.model_dump()})
        return value

    def review_loop(self, base, draft, material, text, meta, log_dir, revision=0, first_feedback=None):
        max_revisions = self.config['pipeline']['max_revisions']
        cps = self.config['pipeline']['chars_per_sec']
        history = []
        def revise(payload):
            changed = self.call('revise', payload, RevisionResult, log_dir, material.production_type)
            changed_material = changed.material if changed.material is not None else material
            changed_material, alignments = align_material_quotes(changed_material, text)
            if alignments:
                atomic_json(log_dir / f'{len(self.events):02d}-revise-quote-alignment.json', alignments)
            validate_material(changed_material, text, meta)
            if changed_material.production_type != material.production_type or changed_material.category != material.category:
                raise ValueError('revision_cannot_change_route')
            if not {f.id for f in material.facts}.issubset({f.id for f in changed_material.facts}):
                raise ValueError('revision_cannot_remove_fact_ids')
            base['material'] = changed_material.model_dump()
            return changed.draft, changed_material
        if first_feedback:
            if revision >= max_revisions:
                raise ValueError('revision_budget_exhausted')
            payload = {**base, 'draft': draft.model_dump(), 'feedback': [i.model_dump() for i in first_feedback]}
            draft, material = revise(payload)
            revision += 1
        while True:
            # Listening format is deterministic metadata, not a creative choice.
            draft.format = {'창작·각색형': '이야기형', '사실해설형': '정보형',
                            '사실서사형': '이야기형', '실행안내형': '안내형',
                            '해석·감상형': '감상형', '원문낭독형': '본문낭독형'}[material.production_type]
            deterministic = checks(draft, material, text, meta, cps, self.config['pipeline'].get('character_count_mode', 'non_whitespace'), min_sec=self.config['pipeline'].get('duration_min_sec'), max_sec=self.config['pipeline'].get('duration_max_sec'))
            payload = {**base, 'draft': draft.model_dump(), 'checks': [i.model_dump() for i in deterministic],
                       'revision': revision, 'previous_reviews': history}
            brief = base.get('editorial_brief', [])
            if brief:
                audited = self.call('review', payload, BriefReviewResult, log_dir, material.production_type)
                review = audited.review
                review.editorial_checks = audited.editorial_checks
                review.issues.extend(editorial_issues(review, brief))
            else:
                review = self.call('review', payload, Review, log_dir, material.production_type)
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
            history.append({'revision': revision, **review.model_dump()})
            atomic_json(log_dir / f'revision-{revision}.json', {'draft': draft.model_dump(), 'review': review.model_dump()})
            if review.verdict != 'revise' or revision >= max_revisions:
                return draft, review, revision, history, material
            draft, material = revise({**base, 'draft': draft.model_dump(), 'feedback': review.model_dump(),
                                      'previous_reviews': history})
            revision += 1

    def run(self, text, meta, digest, destination: Path, *, editorial_brief=None, source_file=None):
        editorial_brief = validate_editorial_brief(editorial_brief)
        self.events = []
        content_id = 'S2-' + digest[:16]
        if destination == self.root / 'output':
            destination = output_directory(self.root, meta.source_id)
        log_dir = log_directory(self.root, destination, content_id)
        source_id = meta.source_id or 'src-' + digest[:16]
        source_path = (os.path.relpath(source_file.resolve(), destination.resolve()) if source_file is not None
                       else source_reference(self.root, destination, source_id, digest))
        base = {'source_text': text, 'metadata': meta.model_dump(), 'target_sec': meta.target_sec,
                'target_chars': round(meta.target_sec * self.config['pipeline']['chars_per_sec'])}
        if editorial_brief:
            base['editorial_brief'] = editorial_brief
        material = self.call('analyze', base, Material, log_dir, meta.production_type)
        material, alignments = align_material_quotes(material, text)
        if alignments:
            atomic_json(log_dir / 'analysis-quote-alignment.json', alignments)
        validate_material(material, text, meta)
        resolved, rights_notes, rights = resolve_rights(meta.model_copy(update={'production_type': material.production_type}))
        base['metadata'] = resolved.model_dump()
        atomic_json(log_dir / 'material.json', material.model_dump())
        base['material'] = material.model_dump()
        draft = self.call('generate', base, Draft, log_dir, material.production_type)
        draft, review, revision, history, material = self.review_loop(base, draft, material, text, meta, log_dir)
        source = {'source_id': source_id, 'source_url': meta.source_url, 'author': meta.author,
                  'license': resolved.license.model_dump(), 'attribution': resolved.attribution,
                  'source_kind': resolved.source_kind,
                  'original_sources': [s.model_dump() for s in resolved.original_sources],
                  'text_path': str(source_path), 'text': text, 'info_as_of': meta.info_as_of,
                  'collected_at': meta.collected_at, 'edition': meta.edition,
                  'sha256': hashlib.sha256(text.encode()).hexdigest()}
        source = fact_metadata(source)
        status = 'ready_for_downstream_review' if review.verdict == 'pass' else 'hold'
        if rights in {'restricted', 'unverified'}:
            status = 'hold'
        output = {'schema_version': '1.0.0', 'content_id': content_id, 'created_at': now(),
                  'title': draft.title, 'category': material.category, 'format': draft.format,
                  'production_type': material.production_type, 'tags': draft.tags,
                  'version': f'1.0.{revision}', 'target_sec': meta.target_sec,
                  'script': draft.script, 'sources': [source], 'material': material.model_dump(),
                  'claims': [c.model_dump() for c in draft.claims],
                  'adaptation_note': draft.adaptation_note.model_dump(),
                  'license': resolved.license.kind, 'attribution': resolved.attribution, 'source_url': meta.source_url,
                  'generator': {'model': self.config['models']['generator']['model'],
                      'effort': self.config['models']['generator']['effort'],
                      'prompt_version': self.config['pipeline']['prompt_version'], 'revision': revision,
                      'feedback_revision': 0,
                      'self_check_model': self.config['models']['reviewer']['model'],
                      'revision_model': self.config['models']['generator']['model'],
                      'self_check_effort': self.config['models']['reviewer']['effort'], 'calls': self.events},
                  'status': status, 'rights_status': rights, 'rights_notes': rights_notes, 'input_sha256': digest,
                  'self_review': history, 'editorial_brief': editorial_brief, 'unresolved': draft.unresolved + review.remaining,
                  'duration': {'estimated_sec': estimated_duration(draft, self.config['pipeline']['chars_per_sec'], self.config['pipeline'].get('character_count_mode', 'non_whitespace')),
                               'measured_sec': None, 'method': self.config['pipeline'].get('character_count_mode', 'non_whitespace') + '_chars / chars_per_sec (user-calibrated estimate; not measured audio)',
                               'chars_per_sec': self.config['pipeline']['chars_per_sec']},
                  'fixture': meta.fixture, 'input_metadata': meta.model_dump(),
                  'downstream_approval': 'pending', 'tts_checked': False}
        apply_use_assessment(apply_team_copyright(apply_news_attribution(output)))
        Handoff.model_validate(output)
        atomic_json(destination / f'{content_id}.json', output)
        return output

    def apply_feedback(self, output_path, issues, expected_version=None, *, feedback_normalizations=None):
        from .models import SourceMeta
        self.events = []
        out = json.loads(output_path.read_text(encoding='utf-8'))
        Handoff.model_validate(out)
        if expected_version is not None and out['version'] != expected_version:
            raise ValueError('feedback_version_mismatch')
        revision = out['generator']['revision']
        feedback_revision = out['generator'].get('feedback_revision', 0)
        if feedback_revision >= self.config['pipeline']['max_revisions']:
            raise ValueError('revision_budget_exhausted')
        if not issues or any(i.sentence > len(out['script']) for i in issues):
            raise ValueError('invalid_feedback_sentence')
        source = out['sources'][0]
        text = source['text']
        meta = SourceMeta.model_validate(out['input_metadata'])
        material = Material.model_validate(out['material'])
        resolved, rights_notes, rights = resolve_rights(meta.model_copy(update={'production_type': material.production_type}))
        draft = Draft.model_validate({k: out[k] for k in ('title','format','tags','script','claims','adaptation_note','unresolved')} | {'changes': []})
        base = {'source_text': text, 'metadata': resolved.model_dump(), 'material': material.model_dump(),
                'target_sec': meta.target_sec, 'target_chars': round(meta.target_sec * self.config['pipeline']['chars_per_sec'])}
        if out.get('editorial_brief'):
            base['editorial_brief'] = validate_editorial_brief(out['editorial_brief'])
        external_run = (f'batch-feedback-{feedback_revision + 1}' if self.config['pipeline'].get('mode') == 'batch'
                        else now().replace(':', '-') + f'-feedback-{feedback_revision + 1}')
        log_dir = log_directory(self.root, output_path.parent, out['content_id']) / ('external-' + external_run)
        if self.config['pipeline'].get('mode') == 'batch':
            contract = {'input_sha256': hashlib.sha256(json.dumps(out, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                        'issues': [i.model_dump() for i in issues], 'normalizations': feedback_normalizations}
            contract_path = log_dir / 'feedback-contract.json'
            if contract_path.exists() and json.loads(contract_path.read_text()) != contract:
                raise ValueError('feedback_checkpoint_changed_requires_review')
            atomic_json(contract_path, contract)
        if feedback_normalizations:
            atomic_json(log_dir / 'feedback-normalizations.json', feedback_normalizations)
        # Each external round has its own bounded review loop. Its local revision
        # count must not consume another external feedback round.
        draft, review, _, history, material = self.review_loop(
            base, draft, material, text, meta, log_dir, 0, issues)
        feedback_revision += 1
        for record in history:
            record['feedback_round_revision'] = record['revision']
            record['feedback_revision'] = feedback_revision
            record['revision'] = revision
        atomic_json(log_dir / 'before.json', out)
        out.update({k: v for k, v in draft.model_dump().items() if k != 'changes'})
        out['version'] = f'1.0.{revision + feedback_revision}'
        out['updated_at'] = now()
        out['generator']['feedback_revision'] = feedback_revision
        out['generator']['revision_model'] = self.config['models']['generator']['model']
        out['generator']['self_check_model'] = self.config['models']['reviewer']['model']
        out['generator']['self_check_effort'] = self.config['models']['reviewer']['effort']
        out['generator']['calls'].extend(self.events)
        out['generator']['revision_prompt_version'] = self.config['pipeline']['prompt_version']
        out['self_review'].extend(history)
        out['material'] = material.model_dump()
        source.update(license=resolved.license.model_dump(), attribution=resolved.attribution,
                      source_kind=resolved.source_kind,
                      original_sources=[s.model_dump() for s in resolved.original_sources])
        out['sources'][0] = fact_metadata(source)
        out.update(license=resolved.license.kind, attribution=resolved.attribution,
                   rights_status=rights, rights_notes=rights_notes)
        out['status'] = 'ready_for_downstream_review' if review.verdict == 'pass' and out['rights_status'] == 'metadata_supplied_not_legal_approval' else 'hold'
        out['duration'].update(
            estimated_sec=estimated_duration(draft, self.config['pipeline']['chars_per_sec'], self.config['pipeline'].get('character_count_mode', 'non_whitespace')),
            chars_per_sec=self.config['pipeline']['chars_per_sec'],
            method=self.config['pipeline'].get('character_count_mode', 'non_whitespace') + '_chars / chars_per_sec (user-calibrated estimate; not measured audio)',
            measured_sec=None,
        )
        apply_use_assessment(apply_team_copyright(apply_news_attribution(out)))
        Handoff.model_validate(out)
        atomic_json(output_path, out)
        return out


def parse_feedback(path: Path, content_id=None, *, normalization_notes=None):
    raw = path.read_text(encoding='utf-8-sig')
    if path.suffix.lower() == '.jsonl':
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    else:
        rows = json.loads(raw)
        if isinstance(rows, dict) and 'script_id' in rows:
            rows = [rows]
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError('feedback_must_be_nonempty_list')
    envelopes = any('script_id' in row or 'items' in row for row in rows)
    if envelopes:
        if content_id is None or any('script_id' not in row or 'items' not in row for row in rows):
            raise ValueError('feedback_content_id_required')
        selected = [row for row in rows if row['script_id'] == content_id]
        if len(selected) != 1:
            raise ValueError('feedback_content_id_missing_or_duplicate')
        selected = selected[0]
        if selected.get('decision') != 'revise':
            raise ValueError('feedback_requires_human_review')
        rows = selected['items']
        if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
            raise ValueError('feedback_must_be_nonempty_list')
    aliases = {'문장 번호': 'sentence', '문장번호': 'sentence', '문제': 'problem', '이유': 'reason', '수정 방향': 'direction', '수정방향': 'direction'}
    if envelopes:
        aliases.update(sent='sentence', fix='direction')
    issues, notes = [], []
    for item_number, row in enumerate(rows, 1):
        scope = row.get('scope')
        if 'scope' in row and scope not in ('script', 'sentence'):
            raise ValueError('invalid_feedback_scope')
        normalized = {}
        sentence_fields = {}
        for key, value in row.items():
            if key == 'scope':
                continue
            if envelopes and key not in {'sentence', 'sent', 'problem', 'reason', 'direction', 'fix'}:
                continue
            original_key = key
            key = aliases.get(key, key)
            if key == 'sentence':
                if value is not None and (type(value) is not int or value < 0):
                    raise ValueError('invalid_feedback_sentence')
                sentence_fields[original_key] = value
            if key in normalized and normalized[key] != value:
                raise ValueError('feedback_alias_conflict')
            normalized[key] = value
        # Missing scope is the legacy contract; only explicit null means global.
        if 'sentence' not in normalized and scope != 'script':
            raise ValueError('invalid_feedback_sentence')
        number = normalized.get('sentence')
        if scope == 'sentence' and (number is None or number == 0):
            raise ValueError('invalid_feedback_sentence')
        if scope == 'script' and number not in (None, 0):
            raise ValueError('feedback_scope_conflict')
        if number is None:
            normalized['sentence'] = 0
            notes.append({'item': item_number, 'scope': scope or 'legacy',
                          'original_fields': sentence_fields, 'sentence': 0})
        issues.append(Issue.model_validate(normalized))
    if normalization_notes is not None:
        normalization_notes.extend(notes)
    return issues
