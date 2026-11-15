import json
import pytest
from script_agent import cli


def test_long_original_requires_explicit_limit_without_changing_config(tmp_path,monkeypatch,capsys):
    source=tmp_path/'long.md'
    body='원문을 그대로 보존합니다.\n'*5000
    source.write_text(body,encoding='utf-8')
    assert 60000<len(body)<120000
    monkeypatch.setattr(cli,'load_config',lambda _: {'pipeline':{'max_source_chars':60000}})
    monkeypatch.setattr('sys.argv',['script-agent','validate-input',str(source)])
    assert cli.main()==2
    assert json.loads(capsys.readouterr().err)['code']=='empty_or_large_source'
    monkeypatch.setattr('sys.argv',['script-agent','--max-source-chars','256000','validate-input',str(source)])
    assert cli.main()==0
    assert json.loads(capsys.readouterr().out)['characters']==len(body)
    assert source.read_text(encoding='utf-8')==body


@pytest.mark.parametrize('limit',['0','256001'])
def test_explicit_source_limit_is_bounded(monkeypatch,limit):
    monkeypatch.setattr(cli,'load_config',lambda _: {'pipeline':{'max_source_chars':60000}})
    monkeypatch.setattr('sys.argv',['script-agent','--max-source-chars',limit,'validate-input','unused.md'])
    with pytest.raises(SystemExit) as exc:cli.main()
    assert exc.value.code==2
