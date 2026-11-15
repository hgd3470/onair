"""Team-owned expression policy; never a grant over third-party source articles."""
POLICY = 'collector-team-copyright-v2'
LICENSE = 'All rights reserved'
COPYRIGHT = '© 틈(TEUM) 팀. All rights reserved.'
NOTES = (
    f'[{POLICY}] {COPYRIGHT}',
    f'[{POLICY}] 팀이 보유한 대본·자체 작성 사실 자료의 표현과 편집에 대한 권리를 유보한다. 제3자의 복제·수정·배포·상업 이용은 별도 허락이 필요하다.',
    f'[{POLICY}] 객관적 사실 자체 및 원기사·원문 인용·사진·음원 등 제3자 권리는 포함하지 않는다.',
    f'[{POLICY}] 원기사 이용 조건·수집·저장·모델 전송 권한 및 검토·게시 상태는 별도로 유지한다.',
)


def apply_team_copyright(out):
    """Declare only the team's output rights; preserve input licenses and review gates."""
    from .fact_source import is_independent_fact_source
    meta = out['input_metadata']
    text = out['sources'][0]['text']
    if (meta.get('author') != '이슈 수집 에이전트'
            or out['production_type'] == '원문낭독형'
            or not text.startswith(('이슈 수집 에이전트 · 사실 인계 v2\n',
                                    '이슈 수집 에이전트 · 사실 인계 v3\n',
                                    '이슈 수집 에이전트 · 사실 인계 v4\n',
                                    '이슈 수집 에이전트 · 원문 인계 v5\n',
                                    '이슈 수집 에이전트 · 원문 인계 v6\n'))
            or not (is_independent_fact_source(meta) or meta.get('source_kind') == 'user_summary')):
        return out
    out['license'] = LICENSE
    out['rights_notes'] = [n for n in out.get('rights_notes', [])
                           if not n.startswith('[collector-script-cc0-v1]')]
    out['rights_notes'].extend(n for n in NOTES if n not in out['rights_notes'])
    return out
