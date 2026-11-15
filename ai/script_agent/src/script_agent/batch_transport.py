"""Durable Gemini Developer API batches for dependent single requests.

Kept identical in script_agent and issue_collector so either installs alone.
POST is never retried; GET/download can resume from the saved remote job ID.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import time

import httpx
from filelock import FileLock


class BatchError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class BatchPending(BatchError):
    """Only remote-job retrieval needs retry; do not finalize the local job."""


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _name(value, kind):
    if not isinstance(value, str) or not re.fullmatch(kind + r'/[A-Za-z0-9_.-]+', value):
        raise BatchError('invalid_batch_resource_name')
    return value


def _get(url, headers, timeout, *, text=False):
    try:
        response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=False)
        if response.status_code != 200:
            raise BatchPending('batch_retrieval_http_' + str(response.status_code))
        return response.text if text else response.json()
    except (httpx.HTTPError, ValueError):
        raise BatchPending('batch_retrieval_failed') from None


def generate(request, *, model, key, path, timeout=300, poll_interval=15, wait_timeout=86400):
    """Return raw GenerateContentResponse, preserving intent, ID and completion."""
    path = Path(path)
    if not key:
        raise BatchError('batch_requires_gemini_api_key')
    if poll_interval <= 0 or wait_timeout < 0 or timeout <= 0:
        raise BatchError('invalid_batch_settings')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', model):
        raise BatchError('invalid_batch_model')
    contract = {'model': model, 'request': request}
    encoded = json.dumps(contract, sort_keys=True, ensure_ascii=False).encode()
    if len(encoded) > 19_000_000:
        raise BatchError('batch_inline_request_too_large')
    sha = hashlib.sha256(encoded).hexdigest()
    base = 'https://generativelanguage.googleapis.com/v1beta'
    headers = {'x-goog-api-key': key}
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock', timeout=0):
        if path.exists():
            saved = json.loads(path.read_text())
            if saved.get('request_sha256') != sha or saved.get('contract') != contract:
                raise BatchError('batch_contract_changed_use_new_destination')
        else:
            saved = {'state': 'prepared', 'request_sha256': sha, 'contract': contract}
            _save(path, saved)
        if saved['state'] == 'rejected':
            raise BatchError(saved['code'])
        if not saved.get('name'):
            if saved['state'] != 'prepared':
                raise BatchError('batch_submission_unknown')
            saved['state'] = 'submitting'
            _save(path, saved)
            body = {'batch': {'displayName': 'teum-' + sha[:24], 'inputConfig': {
                'requests': {'requests': [{'request': request, 'metadata': {'key': sha}}]}}}}
            try:
                response = httpx.post(base + '/models/' + model + ':batchGenerateContent',
                    headers={**headers, 'Content-Type': 'application/json'}, json=body,
                    timeout=timeout, follow_redirects=False)
                if 400 <= response.status_code < 500 and response.status_code != 408:
                    saved.update(state='rejected', code='batch_submission_http_' + str(response.status_code))
                    _save(path, saved)
                    raise BatchError(saved['code'])
                if not 200 <= response.status_code < 300:
                    raise BatchError('batch_submission_unknown')
                name = _name(response.json().get('name'), 'batches')
            except (httpx.HTTPError, ValueError, AttributeError):
                raise BatchError('batch_submission_unknown') from None
            saved.update(name=name, state='submitted')
            _save(path, saved)
        name = _name(saved['name'], 'batches')
        started = time.monotonic()
        while 'terminal' not in saved:
            data = _get(base + '/' + name, headers, timeout)
            state = data.get('state') or data.get('metadata', {}).get('state', '')
            if state.endswith(('_SUCCEEDED', '_FAILED', '_CANCELLED', '_EXPIRED')) or data.get('done'):
                if data.get('error') or state not in {'JOB_STATE_SUCCEEDED', 'BATCH_STATE_SUCCEEDED'}:
                    # Provider error text may echo credentials or input bodies.
                    data = {'state': state, 'error': True}
                saved.update(state='terminal', terminal=data)
                _save(path, saved)
                break
            if time.monotonic() - started >= wait_timeout:
                raise BatchPending('batch_polling_timeout')
            time.sleep(min(poll_interval, max(0, wait_timeout - (time.monotonic() - started))))
        data = saved['terminal']
        state = data.get('state') or data.get('metadata', {}).get('state', '')
        if data.get('error') or state not in {'JOB_STATE_SUCCEEDED', 'BATCH_STATE_SUCCEEDED'}:
            raise BatchError('batch_job_failed')
        if 'items' not in saved:
            response = data.get('response', {})
            dest = data.get('dest', {})
            items = response.get('inlinedResponses') or dest.get('inlinedResponses') or []
            if isinstance(items, dict):
                items = items.get('inlinedResponses', [])
            filename = response.get('responsesFile') or dest.get('fileName')
            if not items and filename:
                filename = _name(filename, 'files')
                content = _get('https://generativelanguage.googleapis.com/download/v1beta/' +
                    filename + ':download?alt=media', headers, timeout, text=True)
                try:
                    items = [json.loads(line) for line in content.splitlines() if line.strip()]
                except ValueError:
                    raise BatchError('invalid_batch_result') from None
            if isinstance(items, list):
                items = [{**item, 'error': True} if item.get('error') else item for item in items]
            saved['items'] = items
            _save(path, saved)
        items = saved['items']
        if (not isinstance(items, list) or len(items) != 1 or
            (items[0].get('metadata', {}).get('key') or items[0].get('key')) != sha):
            raise BatchError('batch_result_missing_or_duplicate')
        item = items[0]
        if item.get('error') or not isinstance(item.get('response'), dict):
            raise BatchError('batch_item_error')
        return item['response']


def clean_gemini_schema(schema):
    """Inline $defs/$ref and drop fields Gemini's responseSchema rejects (kept local: this file is copied as-is)."""
    defs = schema.get('$defs', {})

    def resolve(node, parent_key=None):
        if isinstance(node, dict):
            if '$ref' in node and node['$ref'].split('/')[-1] in defs:
                extra = {k: resolve(v, k) for k, v in node.items() if k != '$ref'}
                return {**resolve(defs[node['$ref'].split('/')[-1]], parent_key), **extra}
            return {k: resolve(v, k) for k, v in node.items() if k not in {'$defs', 'additionalProperties'}
                    and not (k == 'title' and isinstance(v, str) and parent_key != 'properties')}
        if isinstance(node, list):
            return [resolve(v, parent_key) for v in node]
        return node
    return resolve(schema)


def request_body(system, payload, settings, schema):
    # Batch requests did not enforce responseJsonSchema (11/14 run returned {"topics":[]} without the
    # required coverage_notes). Send the inlined OpenAPI-style responseSchema, as the Vertex path does.
    return {'systemInstruction': {'parts': [{'text': system}]},
            'contents': [{'role': 'user', 'parts': [{'text': json.dumps(payload, ensure_ascii=False)}]}],
            'generationConfig': {'thinkingConfig': {'thinkingLevel': settings['effort']},
                'maxOutputTokens': settings['max_output_tokens'],
                'responseMimeType': 'application/json', 'responseSchema': clean_gemini_schema(schema.model_json_schema())}}


def response_text(data):
    candidate = (data.get('candidates') or [{}])[0]
    if candidate.get('finishReason') != 'STOP':
        raise BatchError('incomplete_or_blocked')
    return ''.join(p.get('text', '') for p in candidate.get('content', {}).get('parts', []) if not p.get('thought'))
