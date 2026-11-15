"""음성 명령 → 앱이 할 동작. 의도 규칙(rules)으로 문장을 나누고 검색·청취 기록으로 대상을 고른다."""
from teum.intent.rules import parse
from teum.search.index import TITLE_COVER_MIN


def resolve(text, user_id, catalog, index, listening):
    parsed = parse(text)
    out = {"input": text, "parsed": parsed, "action": None, "candidates": [], "message": None}
    if parsed["intent"] in ("play_content", "resume_content"):
        hist = listening.history(user_id, catalog)
        if not parsed["query"]:
            unfinished = [h for h in hist.values() if not h["finished"] and h["last_position_sec"] > 0]
            if parsed["intent"] == "resume_content" and unfinished:  # "계속 들려줘": 가장 최근에 멈춘 것
                h = max(unfinished, key=lambda x: x["last_ts"])
                c = catalog.by_id[h["content_id"]]
                out["action"] = {"type": "play", "content_id": c["content_id"], "title": c["title"],
                                 "start_at_sec": h["last_position_sec"], "audio_uri": c["audio_uri"]}
                out["message"] = "대상을 말하지 않아 가장 최근에 멈춘 콘텐츠를 이어 틂"
                return out
            out["message"] = "무엇을 틀지 다시 물어봐야 함"
            parsed["requires_disambiguation"] = True
            return out
        restrict = set(hist) if parsed["scope"] == "listening_history" else None
        res = index.search(parsed["query"], k=3, restrict_ids=restrict)
        if not res and restrict is not None:  # 이력에 없으면 전체에서 한 번 더
            res = index.search(parsed["query"], k=3)
            out["message"] = "청취 이력에서 못 찾아 전체 카탈로그에서 찾음"
        out["candidates"] = res
        if not res:
            parsed["requires_disambiguation"] = True
            out["message"] = "맞는 콘텐츠가 없음 → '그 이야기는 아직 없어요' 안내"
            return out
        said_title = res[0]["title_coverage"] >= TITLE_COVER_MIN  # 제목을 말했으면 되묻지 않는다(v2 시나리오에서 I05로 확인)
        said_topic = res[0]["matched_field"] == "분류·태그"  # "역사 이야기"는 그 분류에서 첫 번째를 바로 튼다
        if not said_title and not said_topic and len(res) > 1 and res[0]["dense_score"] is not None and res[1]["dense_score"] is not None \
                and res[0]["dense_score"] - res[1]["dense_score"] < 0.02:
            parsed["requires_disambiguation"] = True
            out["message"] = "후보가 비슷함 → 어느 쪽인지 되묻기"
            return out
        top = res[0]["content_id"]
        pos = 0.0
        if parsed["intent"] == "resume_content" and top in hist and not hist[top]["finished"]:
            pos = hist[top]["last_position_sec"]
        out["action"] = {"type": "play", "content_id": top, "title": catalog.by_id[top]["title"], "start_at_sec": pos,
                         "audio_uri": catalog.by_id[top]["audio_uri"], "attribution": catalog.by_id[top].get("attribution"),
                         "license": catalog.by_id[top].get("license")}
    elif parsed["intent"] in ("skip", "pause", "like"):
        out["action"] = {"type": parsed["intent"]}
    else:
        out["message"] = "명령을 이해하지 못함 → 다시 말해 달라고 안내"
    return out
