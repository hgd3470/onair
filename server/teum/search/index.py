"""콘텐츠 검색 (S2-08).

검색 대상 = 카탈로그에서 published 상태인 콘텐츠의 제목·분류·태그·대본. 수집 원문은 대상이 아니다.
- bm25  : 글자 2-gram BM25. 제목·대본에 나온 단어가 겹칠 때 강함
- dense : BGE-m3-ko 임베딩 코사인 유사도. 말을 바꿔 물어도 찾음
- hybrid: 두 순위를 RRF로 합침
"""
import json
import math
from collections import Counter

from teum.core.text import CATEGORY_KO, normalize_for_match

DENSE_MIN_SCORE = 0.45   # 이보다 낮으면 '결과 없음'으로 본다. 시범 운용 임시값(평가셋으로 다시 정해야 함)
RRF_K = 60
TITLE_COVER_MIN = 0.6    # 질의 글자쌍의 60% 이상이 한 제목에 있으면 '제목을 말한 것'으로 본다
MODES = ("bm25", "dense", "hybrid")


def bigrams(text):
    t = normalize_for_match(text)
    return [t[i:i + 2] for i in range(len(t) - 1)]


def doc_text(r):
    return f"{r['title']}\n{CATEGORY_KO.get(r['category'], r['category'])}\n{' '.join(r['tags'])}\n{r['script']}"


# "역사 이야기 들려줘"처럼 분류나 태그를 말한 요청. 짧은 질의는 임베딩 점수가 낮아(역사 0.4 미만) '없음'으로 잘렸다(10/27 차량 시연)
CATEGORY_ALIASES = {"funny": ["재미있는", "재밌는", "웃긴", "썰"], "emotional": ["감동", "찡한"], "motivation": ["동기부여", "힘나는"],
                    "insight": ["인사이트", "교양"], "economy": ["시사", "경제"], "history": ["역사"], "science": ["과학"], "life": ["생활"]}

CATEGORY_ALIASES.update({
    "C01": ["직장", "회사"], "C02": ["연애", "결혼", "소개팅"],
    "C03": ["가족", "육아"], "C04": ["친구", "인간관계"],
    "C05": ["동기부여", "마음", "습관", "성장"], "C06": ["생활", "살림", "자취"],
    "C07": ["음식", "맛집", "식문화"], "C08": ["경제", "돈", "소비"],
    "C09": ["건강", "운동", "생활리듬"], "C10": ["반려동물", "자연"],
    "C11": ["여행", "지역", "문화"], "C12": ["패션", "뷰티", "자기표현"],
    "C13": ["과학", "기술", "AI", "우주"], "C14": ["역사", "인물"],
    "C15": ["인문", "독서", "철학"], "C16": ["시사", "뉴스", "사회"],
    "C17": ["사건", "미스터리", "추리"], "C18": ["음악", "영화", "예술"],
    "C19": ["스포츠", "게임", "운동"], "C20": ["운전", "자동차", "교통"],
    "C21": ["성경", "말씀", "시편", "신앙"],
})


def category_words(d):
    ko = CATEGORY_KO.get(d["category"], "")
    # 분류 이름을 쪼갠 한 글자('일·직장'의 '일')는 질문 속 다른 단어에 섞여 잘못 걸리므로 뺀다. 직접 정한 별칭('썰')은 둔다.
    names = {normalize_for_match(w) for w in ko.replace("·", " ").split()}
    return {w for w in names if len(w) >= 2} | {normalize_for_match(w) for w in CATEGORY_ALIASES.get(d["category"], [])}


class SearchIndex:
    def __init__(self, contents, use_dense=True, index_dir=None, dense_model=None):
        self.index_dir, self.dense_model = index_dir, dense_model
        self.docs = [c for c in contents if c["kind"] == "story"]
        self.ids = [d["content_id"] for d in self.docs]
        self.fields = {d["content_id"]: {"title": Counter(bigrams(d["title"])), "script": Counter(bigrams(d["script"]))} for d in self.docs}
        self.tf = [Counter(bigrams(doc_text(d))) for d in self.docs]
        self.len = [sum(t.values()) for t in self.tf]
        self.avg = sum(self.len) / max(len(self.len), 1)
        df = Counter()
        for t in self.tf:
            df.update(t.keys())
        n = len(self.docs)
        self.idf = {g: math.log(1 + (n - f + 0.5) / (f + 0.5)) for g, f in df.items()}
        self.cat_kw = [category_words(d) for d in self.docs]
        self.tag_kw = [{normalize_for_match(t) for t in d["tags"] if len(t) >= 2} for d in self.docs]
        self.model, self.emb = None, None
        if use_dense:
            self._load_dense()

    def _load_dense(self):
        emb_path = self.index_dir / "embeddings.npy"
        ids_path = self.index_dir / "ids.json"
        if not emb_path.exists():
            return
        import numpy as np
        from sentence_transformers import SentenceTransformer
        ids = json.loads(ids_path.read_text())
        emb = np.load(emb_path)
        pos = {cid: i for i, cid in enumerate(ids)}
        keep = [pos[c] for c in self.ids if c in pos]
        if len(keep) != len(self.ids):
            raise RuntimeError("임베딩 색인과 카탈로그가 어긋남. models/build_index.py를 다시 실행")
        self.emb = emb[keep]
        self.model = SentenceTransformer(self.dense_model, device="cpu")

    def _bm25(self, q, k1=1.5, b=0.75):
        qg = bigrams(q)
        scores = []
        for tf, dl in zip(self.tf, self.len):
            s = 0.0
            for g in qg:
                f = tf.get(g, 0)
                if f:
                    s += self.idf[g] * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / self.avg))
            scores.append(s)
        return scores

    def _dense(self, q):
        import numpy as np
        qv = self.model.encode([q], normalize_embeddings=True)[0]
        return list(np.asarray(self.emb) @ qv)

    def matched_field(self, cid, q):
        qg = set(bigrams(q))
        f = self.fields[cid]
        t, s = len(qg & set(f["title"])), len(qg & set(f["script"]))
        if t == 0 and s == 0:
            return "의미(임베딩)"
        return "title" if t >= s else "script"

    def title_coverage(self, cid, q):
        qg = set(bigrams(q))
        return len(qg & set(self.fields[cid]["title"])) / len(qg) if qg else 0.0

    def search(self, q, k=5, mode="hybrid", restrict_ids=None, min_dense=DENSE_MIN_SCORE):
        if mode not in MODES:
            raise ValueError(f"mode는 {MODES} 중 하나")
        if mode != "bm25" and self.model is None:
            mode = "bm25"
        idx = [i for i, cid in enumerate(self.ids) if restrict_ids is None or cid in restrict_ids]
        topic = False
        bm = self._bm25(q)
        de = self._dense(q) if mode in ("dense", "hybrid") else None
        if mode == "bm25":
            ranked = sorted(idx, key=lambda i: -bm[i])
            ranked = [i for i in ranked if bm[i] > 0]
            score = {i: bm[i] for i in ranked}
        else:
            # 가장 가까운 것도 멀면 '없음'. 단 제목을 거의 그대로 말했으면 살린다
            # (v1 검증: 공개 중인 I05 제목을 그대로 말해도 임베딩 0.437 < 0.45로 '없음'이 됐다)
            best_cover = max((self.title_coverage(self.ids[i], q) for i in idx), default=0.0)
            qn = normalize_for_match(q)
            if best_cover < TITLE_COVER_MIN:
                # 제목을 말하지 않고 분류("역사", "웃긴 썰")를 말했으면 그 분류 안에서만 고른다
                cat = [i for i in idx if any(w in qn for w in self.cat_kw[i])]
                if cat:
                    idx, topic = cat, True
            if not topic and (not idx or (max(de[i] for i in idx) < min_dense and best_cover < TITLE_COVER_MIN)):
                # 임베딩으로도 멀면 '없음'. 단 태그("블랙홀")를 말했으면 그 콘텐츠를 살린다
                idx = [i for i in idx if any(w in qn for w in self.tag_kw[i])]
                if not idx:
                    return []
                topic = True
            by_dense = sorted(idx, key=lambda i: -de[i])
            if mode == "dense":
                ranked, score = by_dense, {i: float(de[i]) for i in by_dense}
            else:
                by_bm = [i for i in sorted(idx, key=lambda i: -bm[i]) if bm[i] > 0]
                rrf = Counter()
                for r, i in enumerate(by_dense):
                    rrf[i] += 1 / (RRF_K + r + 1)
                for r, i in enumerate(by_bm):
                    rrf[i] += 1 / (RRF_K + r + 1)
                ranked = sorted(idx, key=lambda i: -rrf[i])
                score = {i: rrf[i] for i in ranked}
        out = []
        for i in ranked[:k]:
            d = self.docs[i]
            out.append({"content_id": d["content_id"], "title": d["title"], "relevance_score": round(float(score[i]), 4),
                        "dense_score": round(float(de[i]), 4) if de is not None else None,
                        "matched_field": "분류·태그" if topic else self.matched_field(d["content_id"], q),
                        "title_coverage": round(self.title_coverage(d["content_id"], q), 3), "playable": True})
        return out
