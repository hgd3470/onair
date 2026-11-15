"""Complete explicitly identified license metadata; never infer a grant from prose.

Profiles describe distribution of an adapted script, not all legal uses.
Source applicability still needs collector-supplied evidence and attribution.
References: https://creativecommons.org/licenses/{by,by-sa,by-nc,by-nd}/4.0/
"""
import re

from .models import License, SourceMeta


CC_CODES = ('by', 'by-sa', 'by-nc', 'by-nd', 'by-nc-sa', 'by-nc-nd')


def complete_license(license: License):
    lic = license.model_copy(deep=True)
    notes, conflicts = [], []
    if lic.kind.upper() in {'CC0-1.0','CC0 1.0'}:
        profile = {'kind':'CC0-1.0','version':'1.0','commercial':True,'modify':True,
                   'share_alike':False,'url':'https://creativecommons.org/publicdomain/zero/1.0/'}
        for field,expected in profile.items():
            current=getattr(lic,field)
            if current is None or field=='kind':
                setattr(lic,field,expected)
                if current!=expected: notes.append(f'license.{field}: 명시된 CC0-1.0 조건에서 보완')
            elif (current.rstrip('/') if field=='url' else current) != (expected.rstrip('/') if field=='url' else expected):
                conflicts.append(f'license.{field}와 CC0-1.0 조건 불일치')
        return lic, notes, conflicts
    kind = re.fullmatch(r'CC (BY(?:-NC)?(?:-SA|-ND)?)(?: (4\.0))?', lic.kind.upper())
    url = re.fullmatch(
        r'https://creativecommons\.org/licenses/(by(?:-nc)?(?:-sa|-nd)?)/4\.0/?',
        lic.url or '')
    code = kind[1].lower() if kind else None
    if url:
        if code and code != url[1]:
            conflicts.append('license.kind/url 불일치')
        elif not code and lic.kind.lower() != 'unknown':
            conflicts.append('license.kind/url 불일치')
        else:
            code = url[1]
    version = kind[2] if kind and kind[2] else ('4.0' if url else lic.version)
    if version and lic.version and version != lic.version:
        conflicts.append('license.version 불일치')
    if code not in CC_CODES or version != '4.0' or conflicts:
        return lic, notes, conflicts
    canonical_url = f'https://creativecommons.org/licenses/{code}/4.0/'
    # An unrelated URL is retained and flagged instead of silently replaced.
    if lic.url and lic.url.rstrip('/') != canonical_url.rstrip('/'):
        conflicts.append('license.url과 식별된 표준 조건 불일치')
        return lic, notes, conflicts
    profile = {'kind': 'CC ' + code.upper() + ' 4.0', 'version': '4.0',
               'commercial': 'nc' not in code.split('-'),
               'modify': 'nd' not in code.split('-'),
               'share_alike': 'sa' in code.split('-'), 'url': canonical_url}
    for field, expected in profile.items():
        current = getattr(lic, field)
        if current is None or (field == 'kind' and current.lower() == 'unknown'):
            setattr(lic, field, expected)
            notes.append(f'license.{field}: 명시된 {profile["kind"]} 조건에서 보완')
        elif field in {'commercial', 'modify', 'share_alike'} and current != expected:
            conflicts.append(f'license.{field}와 {profile["kind"]} 조건 불일치')
    return lic, notes, conflicts


def resolve_rights(meta: SourceMeta):
    """Return a copy, completion/missing notes, and the aggregate rights status."""
    resolved = meta.model_copy(deep=True)
    notes, statuses = [], []
    entries = [('source', resolved)] + [
        (f'original_sources[{i}]', source) for i, source in enumerate(resolved.original_sources)]
    for prefix, source in entries:
        source.license, completed, conflicts = complete_license(source.license)
        notes.extend(f'{prefix}.{note}' for note in completed + conflicts)
        lic = source.license
        if lic.kind != 'CC0-1.0' and not source.attribution and source.author and source.source_url:
            source.attribution = f'원문: {source.author} / {source.source_url} / {lic.kind}'
            if lic.url:
                source.attribution += f' / {lic.url}'
            source.attribution += ' / 원문 기반 대본 구성'
            notes.append(f'{prefix}.attribution: 제공된 저자·출처로 구성; 표시 요건 확인 필요')
        missing = []
        if lic.kind.lower() in {'unknown', 'null', 'none'}:
            missing.append('license.kind')
        for name in ('commercial', 'modify', 'share_alike'):
            if getattr(lic, name) is None:
                missing.append(f'license.{name}')
        if not lic.evidence or not lic.evidence.strip():
            missing.append('license.evidence (이 원문에 조건이 적용되는 근거)')
        own_fiction = (prefix == 'source' and lic.kind == '팀 자체 AI 창작물'
                       and meta.source_kind == 'original' and not meta.original_sources
                       and not source.source_url and meta.production_type == '창작·각색형'
                       and meta.category in {'C01','C02','C03','C04','C05'})
        if lic.kind != 'CC0-1.0' and not own_fiction and (not source.attribution or not source.attribution.strip()):
            missing.append('attribution')
        if lic.kind.upper().startswith('CC ') and not lic.version:
            missing.append('license.version')
        if prefix != 'source' and not source.source_url:
            missing.append('source_url')
        if prefix == 'source' and meta.production_type == '원문낭독형' and not meta.edition:
            missing.append('edition')
        notes.extend(f'{prefix}.{field}: 미확인' for field in missing)
        if lic.commercial is False or (lic.modify is False and meta.production_type != '원문낭독형'):
            statuses.append('restricted')
            notes.append(f'{prefix}: 상업 이용 또는 대본 변경 제한')
        elif missing or conflicts:
            statuses.append('unverified')
        else:
            statuses.append('metadata_supplied_not_legal_approval')
    if meta.source_kind == 'user_summary' and not meta.original_sources:
        notes.append('original_sources: 사용자 요약문의 원래 기사·출처와 이용 조건 필요')
        statuses.append('unverified')
    status = ('restricted' if 'restricted' in statuses else
              'unverified' if 'unverified' in statuses else 'metadata_supplied_not_legal_approval')
    return resolved, notes, status
