"""Package explicit project paths, never credentials or installed environments."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--name', default='script-agent.zip')
args = parser.parse_args()
if Path(args.name).name != args.name or not args.name.endswith('.zip'):
    parser.error('name must be a ZIP basename')
files = [root / name for name in (
    'README.md', 'SPEC.md', 'SOURCE_COLLECTION_GUIDE.md', 'pyproject.toml', 'poetry.lock',
    'config.toml', '.gitignore', '.env.example',
)]
for name in ('src', 'prompts', 'examples', 'tests', 'schema', 'tools', 'review_ui'):
    files.extend(p for p in (root / name).rglob('*')
                 if p.is_file() and p.suffix in {'.py', '.sh', '.md', '.txt', '.json', '.jsonl', '.html'}
                 and not any(part.startswith('.') or part == '__pycache__'
                             for part in p.relative_to(root).parts))
files = sorted({p for p in files if not p.is_symlink()
                and '__pycache__' not in p.parts
                and p.name != 'execution.log'
                and not p.name.endswith(('.pyc', '.lock'))
                and (not p.name.startswith('.') or p.name in ('.gitignore', '.env.example'))}
               | {root / 'poetry.lock'})
assert all(p.name != '.env' and not p.name.startswith('.env.')
           or p.name == '.env.example' for p in files)
manifest = {
    'format': 'source_project_zip',
    'execution_results_included': False,
    'credentials_included': False,
    'installed_environments_included': False,
    'files': [{'path': str(p.relative_to(root)), 'bytes': p.stat().st_size,
               'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files],
}
destination = root / 'dist' / args.name
destination.parent.mkdir(exist_ok=True)
prefix = root.name
with ZipFile(destination, 'w', compression=ZIP_DEFLATED, compresslevel=9) as archive:
    for p in files:
        archive.write(p, f'{prefix}/{p.relative_to(root)}')
    for directory in ('input', 'processing', 'failed', 'state/completed', 'logs'):
        archive.writestr(f'{prefix}/{directory}/', '')
    archive.writestr(f'{prefix}/RELEASE-MANIFEST.json',
                     json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
digest = hashlib.sha256(destination.read_bytes()).hexdigest()
destination.with_suffix('.zip.sha256').write_text(f'{digest}  {destination.name}\n')
print(json.dumps({'archive': str(destination), 'files': len(files),
                  'bytes': destination.stat().st_size, 'sha256': digest}))
