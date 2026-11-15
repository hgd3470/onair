"""Project-local credentials and source-only distribution boundaries (no API calls)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest

from script_agent import cli, preflight, providers, reviewer_experiment

ROOT = Path(__file__).resolve().parents[1]
KEY_NAMES = ('openai_api_key', 'gemini_api_key', 'OPENAI_API_KEY', 'GEMINI_API_KEY')


@pytest.fixture
def clean_keys(monkeypatch):
    for name in KEY_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_credentials_use_selected_root_not_cwd_or_parent(tmp_path, monkeypatch, clean_keys):
    project = tmp_path / 'project'
    project.mkdir()
    (tmp_path / '.env').write_text('openai_api_key=parent-test-value\n')
    (project / '.env').write_text('openai_api_key=project-test-value\ngemini_api_key=gemini-test-value\n')
    monkeypatch.chdir(tmp_path)
    assert providers.credentials(root=project) == {
        'openai_api_key': 'project-test-value', 'gemini_api_key': 'gemini-test-value',
    }
    (project / '.env').unlink()
    assert providers.credentials(root=project) == {}
    monkeypatch.chdir(project)
    assert providers.credentials() == {}  # No implicit upward dotenv search.


def test_credentials_explicit_file_and_environment_precedence(tmp_path, monkeypatch, clean_keys):
    (tmp_path / '.env').write_text('openai_api_key=default-test-value\n')
    alternate = tmp_path / 'alternate.env'
    alternate.write_text('openai_api_key=alternate-test-value\ngemini_api_key=gemini-test-value\n')
    assert providers.credentials(alternate, root=tmp_path)['openai_api_key'] == 'alternate-test-value'
    monkeypatch.setenv('OPENAI_API_KEY', 'upper-test-value')
    assert providers.credentials(alternate, root=tmp_path)['openai_api_key'] == 'upper-test-value'
    monkeypatch.setenv('openai_api_key', 'lower-test-value')
    assert providers.credentials(alternate, root=tmp_path)['openai_api_key'] == 'lower-test-value'


@pytest.mark.parametrize('entry', ['run', 'preflight', 'reviewer'])
@pytest.mark.parametrize('explicit', [False, True])
def test_entrypoints_forward_project_root(tmp_path, monkeypatch, entry, explicit):
    project = tmp_path / 'project'
    project.mkdir()
    shutil.copy2(ROOT / 'config.toml', project / 'config.toml')
    # A minimal fixture reaches credential loading; construction stops before any API call.
    cases = project / 'examples/reviewer_cases'
    cases.mkdir(parents=True)
    (cases / 'adaptation.json').write_text('{}')
    env_file = tmp_path / 'custom.env' if explicit else None
    seen = []

    class StopAtCredentials(Exception):
        pass

    def capture_credentials(path=None, *, root=None):
        seen.append((path, root))
        raise StopAtCredentials

    monkeypatch.chdir(tmp_path)
    args = ['script-agent', '--root', str(project)]
    if env_file is not None:
        args += ['--env-file', str(env_file)]
    if entry == 'reviewer':
        monkeypatch.setattr(reviewer_experiment, 'credentials', capture_credentials)
        monkeypatch.setattr(sys, 'argv', args)
        with pytest.raises(StopAtCredentials):
            reviewer_experiment.main()
    else:
        monkeypatch.setattr(preflight if entry == 'preflight' else cli, 'credentials', capture_credentials)
        monkeypatch.setattr(sys, 'argv', args + [entry])
        assert cli.main() == 2
    assert seen == [(env_file, project)]


def test_release_excludes_credentials_runtime_outputs_and_symlinks(tmp_path):
    project = tmp_path / 'script_agent'
    project.mkdir()
    for name in ('README.md', 'SPEC.md', 'SOURCE_COLLECTION_GUIDE.md', 'pyproject.toml', 'poetry.lock', 'config.toml', '.gitignore', '.env.example'):
        shutil.copy2(ROOT / name, project / name)
    for name in ('src', 'prompts', 'examples', 'tests', 'schema', 'tools', 'review_ui'):
        shutil.copytree(ROOT / name, project / name, ignore=shutil.ignore_patterns('__pycache__'))
    sentinel = uuid4().hex
    for name in ('.env', '.env.local', 'output/result.json', 'logs/request.json',
                 'experiments/ledger.json', 'evidence/preflight.json', 'handoff/result.json',
                 'matrix_20261108/result.json', 'input/story.txt', 'archive/story.txt', 'scratch/source.md',
                 'src/.env', 'src/.env.backup.py', 'src/.private/note.txt'):
        file = project / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(sentinel)
    (project / 'src/linked.txt').symlink_to(project / '.env')
    completed = subprocess.run([sys.executable, str(project / 'tools/package_release.py')],
                               check=True, capture_output=True, text=True)
    report = json.loads(completed.stdout)
    with ZipFile(report['archive']) as archive:
        names = {name.removeprefix('script_agent/') for name in archive.namelist()}
        assert {'.env.example', 'poetry.lock', 'src/script_agent/cli.py', 'prompts/v6/generate.txt'} <= names
        assert {'SOURCE_COLLECTION_GUIDE.md', 'review_ui/index.template.html',
                'examples/feedback.refine.jsonl', 'prompts/v7/common.txt'} <= names
        assert '.env' not in names and 'src/linked.txt' not in names
        assert not any(sentinel in archive.read(name).decode('utf-8')
                       for name in archive.namelist() if not name.endswith('/'))
        manifest = json.loads(archive.read('script_agent/RELEASE-MANIFEST.json'))
        assert manifest['execution_results_included'] is False
        assert manifest['credentials_included'] is False
