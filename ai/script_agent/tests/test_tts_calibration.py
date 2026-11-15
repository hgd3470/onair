"""User-measured TTS rate: 500 characters / 60 seconds, not per-output audio measurement."""
from types import SimpleNamespace
from pathlib import Path
import pytest
from script_agent.validation import estimated_duration,script_char_count
from script_agent.io import load_config


def test_five_hundred_characters_are_sixty_seconds():
    draft=SimpleNamespace(script=['가'*500])
    assert estimated_duration(draft,500/60,'with_spaces') == 60.0


def test_spaces_and_sentence_separator_share_the_same_budget():
    draft=SimpleNamespace(script=['가 나','다 라'])
    assert script_char_count(draft,'with_spaces') == 7
    assert script_char_count(draft,'non_whitespace') == 4
    assert estimated_duration(draft,500/60,'with_spaces') == 0.8


def test_default_calibration_and_legacy_mode_are_explicit():
    config=load_config(Path(__file__).resolve().parents[1])
    assert config['pipeline']['chars_per_sec'] == pytest.approx(500/60)
    assert config['pipeline']['character_count_mode'] == 'with_spaces'
    assert estimated_duration(SimpleNamespace(script=['가 '*45])) == 10.0


def test_absolute_news_cap_overrides_thirty_percent_target_tolerance():
    import json
    from script_agent.models import Draft,Material,SourceMeta
    from script_agent.validation import checks
    root=Path(__file__).resolve().parents[1]
    case=json.loads((root/'examples/reviewer_cases/explanation.json').read_text())
    draft=Draft.model_validate(case['draft'])
    draft.script=['가'*25]*30
    draft.claims=[]
    meta=SourceMeta.model_validate(case['metadata']).model_copy(update={'target_sec':90})
    material=Material.model_validate(case['material'])
    issues=checks(draft,material,case['source_text'],meta,500/60,'with_spaces',min_sec=30,max_sec=90)
    assert estimated_duration(draft,500/60,'with_spaces') == 93.5
    assert any(i.problem=='재생 분량 절대 범위 이탈' for i in issues)
    assert not any(i.problem=='목표 분량 이탈' for i in issues)


@pytest.mark.parametrize('bounds',[{'duration_min_sec':91,'duration_max_sec':90},{'duration_max_sec':0},{'duration_max_sec':'90'},{'duration_max_sec':float('inf')}])
def test_invalid_absolute_duration_configuration_is_rejected(tmp_path,bounds):
    import tomllib
    root=Path(__file__).resolve().parents[1]
    text=(root/'config.toml').read_text()
    insertion='\n'.join(f'{key} = {repr(value) if not isinstance(value,str) else chr(34)+value+chr(34)}' for key,value in bounds.items())
    # TOML uses inf, which Python repr also supplies.
    text=text.replace('[pipeline]','[pipeline]\n'+insertion)
    (tmp_path/'config.toml').write_text(text)
    with pytest.raises(ValueError,match='invalid_duration_bounds'):
        load_config(tmp_path)
