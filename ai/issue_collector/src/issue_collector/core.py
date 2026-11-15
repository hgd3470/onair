import hashlib
import json
import os
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

KST = ZoneInfo('Asia/Seoul')

def now():
    return datetime.now(KST).isoformat(timespec='seconds')

def target_day(value=None, instant=None):
    if value:
        return date.fromisoformat(value)
    return (instant or datetime.now(KST)).astimezone(KST).date() - timedelta(days=1)

def window(day):
    return datetime.combine(day, time(), KST), datetime.combine(day + timedelta(days=1), time(), KST)

def digest(value):
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()

def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)

def read(path):
    return json.loads(Path(path).read_text())

def norm(text):
    return re.sub(r'\s+', ' ', text).strip()

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Topic(Model):
    id: str = Field(pattern=r'^[a-z][a-z0-9-]{0,60}$')
    title: str
    reason: str
    importance: int = Field(ge=1, le=5)
    candidate_ids: list[str]
    search_query: str

class Selection(Model):
    topics: list[Topic]
    coverage_notes: list[str]

class Evidence(Model):
    article_id: str
    quote: str = Field(min_length=5, max_length=1000)

class Fact(Model):
    id: str = Field(pattern=r'^F[0-9]+$')
    text: str
    event_date: str
    date_basis: Literal['explicit', 'relative_to_publication', 'unknown']
    kind: Literal['event', 'official_announcement', 'market_value', 'background', 'opinion', 'allegation']
    involves_named_person: bool
    attribution: str
    evidence: list[Evidence]

class Opinion(Model):
    """Optional attributed interpretation; never a cross-checked fact."""
    id: str = Field(pattern=r'^O[0-9]+$')
    article_id: str
    attribution: str = Field(min_length=1)  # publisher only, checked against fetched metadata
    speaker: str  # actual quoted person/organization; empty for the outlet's own analysis
    quote: str = Field(min_length=5, max_length=120)
    text: str = Field(min_length=5, max_length=100)
    mode: Literal['direct', 'indirect']
    related_fact_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)


class OpinionCheck(Model):
    opinion_id: str
    verdict: Literal['approved', 'reject', 'insufficient']
    attribution_correct: bool
    context_preserved: bool
    relevant: bool
    reason: str


class Extraction(Model):
    title: str
    facts: list[Fact]
    excluded: list[str]
    opinions: list[Opinion] = Field(default_factory=list)

class Check(Model):
    fact_id: str
    verdict: Literal['verified', 'conflict', 'insufficient', 'out_of_window', 'opinion']
    supported_article_ids: list[str]
    independent_origins: list[str]
    date_confirmed: bool
    attribution_correct: bool
    reason: str

class Verification(Model):
    topic_confirmed: bool = False
    central_fact_ids: list[str] = Field(default_factory=list)
    checks: list[Check]
    cautions: list[str]
    opinion_checks: list[OpinionCheck] = Field(default_factory=list)
