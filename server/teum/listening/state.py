"""청취 기록 → 콘텐츠별 청취 상태. DB와 상관없는 순수 함수라 따로 테스트한다."""

MIN_PLAYED_RATIO = 0.8  # 데모 config.json의 minimumPlayedRatio: 이만큼 들으면 '들은 것'으로 쳐서 편성에서 뺀다
END_MARGIN = 1.0        # 끝에서 1초 안이면 다 들은 것으로 보고 처음부터 튼다


def fold_content(events, duration, base_version=1, prev_completed=False) -> dict:
    """Fold one user's content events by seq, without changing the input.

    Each play has a fresh playback_id. Version-run transitions and event
    validation/deduplication are the caller's responsibility.
    """
    reactions = {"like": ("liked", True), "unlike": ("liked", False),
                 "save": ("saved", True), "unsave": ("saved", False)}
    position_types = {"play", "position", "pause", "resume", "skip", "complete",
                      "duck_start", "duck_end"}
    result = {
        "last_position_sec": 0.0, "played_sec": 0.0,
        "completed": prev_completed, "finished": False, "skipped": False,
        "liked": False, "saved": False, "last_ts": None,
        "content_version": base_version, "duration_at_version": duration,
        "base_version": base_version, "prev_completed": prev_completed,
    }
    playbacks = {}
    completed_playbacks = set()
    last = None
    for event in sorted(events, key=lambda event: event["seq"]):
        typ = event["type"]
        if typ in reactions:
            field, value = reactions[typ]
            result[field] = value
            continue
        version = event.get("content_version")
        version = 1 if version is None else version
        if version < base_version:
            continue
        playback_id = event.get("playback_id")
        if typ == "skip":
            result["skipped"] = True
        if typ == "complete":
            completed_playbacks.add(playback_id)
        if typ not in position_types or event.get("position_sec") is None:
            continue
        playbacks.setdefault(playback_id, []).append(event)
        last = event
        result.update(last_position_sec=event["position_sec"],
                      last_ts=event["client_ts"], content_version=version)

    intervals = []
    for playback in playbacks.values():
        paused = ducked = ended = False
        previous = None
        for event in playback:
            if previous is not None and not (paused or ducked or ended):
                start, end = previous["position_sec"], event["position_sec"]
                rate = previous.get("playback_rate")
                rate = 1.0 if rate is None else rate
                elapsed = event["client_ts"] - previous["client_ts"]
                if end > start and abs((end - start) - rate * elapsed) <= 1.0:
                    intervals.append((start, end))
            typ = event["type"]
            if typ == "play":
                paused = ducked = ended = False
            elif typ == "pause":
                paused = True
            elif typ == "resume":
                paused = False
            elif typ == "duck_start":
                ducked = True
            elif typ == "duck_end":
                ducked = False
            elif typ in {"skip", "complete"}:
                ended = True
            previous = event

    # Merge across all playbacks so rewinds and repeat listens count only once.
    merged_end = None
    for start, end in sorted(intervals):
        if merged_end is None or start > merged_end:
            result["played_sec"] += end - start
        elif end > merged_end:
            result["played_sec"] += end - merged_end
        merged_end = end if merged_end is None else max(merged_end, end)
    result["completed"] = prev_completed or result["played_sec"] >= MIN_PLAYED_RATIO * duration
    if last is not None:
        result["finished"] = (last.get("playback_id") in completed_playbacks
                              or last["position_sec"] >= duration - END_MARGIN)
    return result
