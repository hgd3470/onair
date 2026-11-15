import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from filelock import FileLock, Timeout

from .models import SourceMeta


def now():
    return datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds')


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def read_source(path: Path, max_chars=160000):
    if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {'.txt', '.md'}:
        raise ValueError('input_must_be_regular_txt_or_md')
    if path.stat().st_size > max_chars * 4:
        raise ValueError('source_too_large')
    raw = path.read_text(encoding='utf-8-sig')
    text, metadata = raw, {}
    if raw.startswith('---\n'):
        parts = raw.split('\n---\n', 1)
        if len(parts) != 2:
            raise ValueError('unclosed_frontmatter')
        metadata, text = yaml.safe_load(parts[0][4:]) or {}, parts[1]
    sidecar = path.with_suffix('.meta.json')
    if sidecar.exists():
        if sidecar.is_symlink() or not sidecar.is_file():
            raise ValueError('sidecar_must_be_regular_file')
        if sidecar.stat().st_size > max_chars * 4:
            raise ValueError('metadata_too_large')
        if metadata:
            raise ValueError('use_frontmatter_or_sidecar_not_both')
        metadata = json.loads(sidecar.read_text(encoding='utf-8'))
    from .news_context import original_bodies, parse_facts
    originals = original_bodies(text)
    if originals is not None:
        parse_facts(text)
    from .news_opinions import parse_opinions
    opinions = parse_opinions(text)
    meta = SourceMeta.model_validate(metadata)
    if originals is not None and (meta.source_kind != 'user_summary'
                                  or len(meta.original_sources) != len(originals)):
        raise ValueError('invalid_news_original_metadata')
    from .news_opinions import HEADER as OPINION_HEADER
    if text.startswith(OPINION_HEADER + '\n'):
        expected = ', '.join(dict.fromkeys(o['attribution'] for o in opinions))
        if meta.attribution != expected:
            raise ValueError('invalid_news_opinion_attribution')
    if not text.strip() or len(text) > max_chars:
        raise ValueError('empty_or_large_source')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and all(re.fullmatch(r'(?:https?://\S+|\[[^\]]+\]\(https?://[^)]+\))', line) for line in lines):
        raise ValueError('source_text_required_not_just_links')
    if meta.reading_text and meta.reading_text not in text:
        raise ValueError('reading_text_not_in_source')
    digest = hashlib.sha256((text + '\0' + json.dumps(meta.model_dump(), sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
    return text, meta, digest


def load_config(root: Path):
    import tomllib
    c = tomllib.loads((root / 'config.toml').read_text(encoding='utf-8'))
    if not 0 <= c['pipeline']['max_revisions'] <= 2:
        raise ValueError('max_revisions_must_be_0_to_2')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', c['pipeline']['prompt_version']):
        raise ValueError('unsafe_prompt_version')
    c.setdefault('pipeline', {})
    c['pipeline'].setdefault('character_count_mode', 'non_whitespace')
    if c['pipeline']['character_count_mode'] not in {'non_whitespace', 'with_spaces'}:
        raise ValueError('invalid_character_count_mode')
    if not isinstance(c['pipeline']['chars_per_sec'], (int,float)) or not 0 < c['pipeline']['chars_per_sec'] <= 100:
        raise ValueError('invalid_chars_per_sec')
    for name in ('duration_min_sec', 'duration_max_sec'):
        bound=c['pipeline'].get(name)
        if bound is not None and (type(bound) not in (int,float) or not 0 < bound <= 600):
            raise ValueError('invalid_duration_bounds')
    lower,upper=c['pipeline'].get('duration_min_sec'),c['pipeline'].get('duration_max_sec')
    if lower is not None and upper is not None and lower > upper:
        raise ValueError('invalid_duration_bounds')
    c['pipeline'].setdefault('mode', 'standard')
    if c['pipeline']['mode'] not in {'standard', 'batch'}:
        raise ValueError('invalid_pipeline_mode')
    c.setdefault('batch', {})
    c['batch'].setdefault('batch_size', 100)
    c['batch'].setdefault('poll_interval_sec', 15)
    c['batch'].setdefault('gpt_workers', 6)
    return c


@contextmanager
def queue_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(root / '.queue.lock'))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise ValueError('queue_already_running') from None
    try:
        yield
    finally:
        lock.release()
