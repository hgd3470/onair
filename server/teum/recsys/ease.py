"""EASE (Steck, WWW 2019 "Embarrassingly Shallow Autoencoders for Sparse Data").

닫힌 해: G = XᵀX + λI, P = G⁻¹, B_ij = -P_ij / P_jj (대각은 0). 사용자 점수 = x_u · B.
콘텐츠가 수백 편이라 numpy 없이 가우스-요르단 역행렬로 충분하다(서버 venv에 numpy 없음).
학습은 새벽 일괄 작업(python -m teum.recsys.train)에서 하고, 서버는 결과 JSON만 읽는다.
"""
import json
from pathlib import Path

MODEL_VERSION = "ease-v1"


def _inverse(m):
    n = len(m)
    a = [row[:] + [float(i == j) for j in range(n)] for i, row in enumerate(m)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError("singular matrix")
        a[col], a[pivot] = a[pivot], a[col]
        p = a[col][col]
        a[col] = [v / p for v in a[col]]
        for r in range(n):
            if r != col and a[r][col]:
                f = a[r][col]
                a[r] = [v - f * w for v, w in zip(a[r], a[col])]
    return [row[n:] for row in a]


def fit(user_items, lam=10.0):
    """user_items: user_id → content_id 집합. 반환: {items, weights(B), lam, users, positives}."""
    items = sorted({c for s in user_items.values() for c in s})
    index = {c: i for i, c in enumerate(items)}
    n = len(items)
    g = [[0.0] * n for _ in range(n)]
    for s in user_items.values():
        ids = [index[c] for c in s]
        for i in ids:
            for j in ids:
                g[i][j] += 1.0
    for i in range(n):
        g[i][i] += lam
    p = _inverse(g) if n else []
    b = [[0.0 if i == j else -p[i][j] / p[j][j] for j in range(n)] for i in range(n)]
    return {"version": MODEL_VERSION, "items": items, "weights": b, "lam": lam,
            "users": len(user_items), "positives": sum(len(s) for s in user_items.values())}


def scores(model, liked_items):
    """사용자가 좋게 반응한 콘텐츠로 다른 콘텐츠 점수를 낸다. 모르는 콘텐츠는 빠진다."""
    index = {c: i for i, c in enumerate(model["items"])}
    rows = [model["weights"][index[c]] for c in liked_items if c in index]
    if not rows:
        return {}
    return {c: sum(r[j] for r in rows) for j, c in enumerate(model["items"])}


def save(model, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(model, ensure_ascii=False))
    tmp.replace(path)


def load(path):
    p = Path(path)
    if not p.exists():
        return None
    model = json.loads(p.read_text())
    return model if model.get("version") == MODEL_VERSION else None
