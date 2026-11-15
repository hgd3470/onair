"""Thread-safe reservations shared by every case under one output root."""
import json,os,threading
from contextlib import contextmanager
from pathlib import Path
_LOCK=threading.RLock()
_PENDING={}
def spent(root):
    # Symlinked historical successes are reused, not billed to this run.
    return sum(json.loads(p.read_text()).get('audit',{}).get('cost_usd',0)
               for p in Path(root).rglob('*.json') if p.parent.name=='cache' and not p.is_symlink())
@contextmanager
def reserve(root,amount):
    root=str(Path(root).resolve())
    with _LOCK:
        if spent(root)+_PENDING.get(root,0)+amount>=float(os.environ.get('REFINE_TOTAL_BUDGET_USD','30')):
            raise RuntimeError('API budget guard: output-root cumulative spend')
        _PENDING[root]=_PENDING.get(root,0)+amount
    try:yield
    finally:
        with _LOCK:_PENDING[root]-=amount
