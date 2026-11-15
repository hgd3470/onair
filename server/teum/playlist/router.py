"""편성 API: 남은 이동 시간에 맞춘 재생 목록."""
from typing import Annotated, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from teum.core.deps import CatalogDep, ListeningDep
from teum.playlist.scheduler import build_playlist
from teum.playlist.recommendation import recommend
from teum.playlist.topics import CATEGORY_CODES, GROUPS
from teum.playlist.policy import PlaylistPolicy
from teum.playlist.policy_provider import get_policy_provider

router = APIRouter(tags=["playlist"])


@router.get("/playlist")
def playlist(user_id: str, catalog: CatalogDep, listening: ListeningDep,
             policy_provider: Annotated[Callable[[], PlaylistPolicy], Depends(get_policy_provider)],
             minutes: float = Query(..., gt=0, le=600, allow_inf_nan=False),
             interests: str = "", speed: float = Query(1.0, gt=0, le=3, allow_inf_nan=False),
             mode: Literal["recommend", "pick", "legacy"] = "recommend"):
    """Existing clients reach the recommendation core without new required fields.

    Listening remains the owner of storage and completion. Topic selections
    (POST /selections + play.selection_id) and user_next skips are read from it.
    """
    if not user_id.strip():
        raise HTTPException(422, "user_id is required")
    ints = list(dict.fromkeys(x.strip() for x in interests.split(",") if x.strip()))
    if not ints:
        ints = listening.interests(user_id)
    history = listening.history(user_id, catalog)
    if mode == "legacy":
        return build_playlist(catalog.items, minutes, ints, history, speed=speed)
    # Keep existing catalog category names compatible during PR #10 migration.
    # Do not guess or write legacy-to-code mappings here.
    valid = CATEGORY_CODES | {c["category"] for c in catalog.stories}
    if set(ints) - valid:
        raise HTTPException(422, "unknown interest category; send expanded C codes")
    if mode == "pick" and not ints:
        raise HTTPException(422, "pick requires interests")
    groups = {g for g, codes in GROUPS.items() if codes.intersection(ints)}
    playable = [c for c in catalog.items
                if (path := catalog.audio_path(c["audio_uri"])) is not None and path.is_file()]
    try:
        policy = policy_provider()
        if not isinstance(policy, PlaylistPolicy):
            raise ValueError("provider must return PlaylistPolicy")
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(503, "playlist policy unavailable") from exc
    response = recommend(playable, minutes, history, interests=ints, groups=groups,
                         speed=speed, mode=mode, selections=listening.selections(user_id),
                         events=listening.playback_events(user_id), policy=policy)
    response["signal_status"] = {
        "interests": "connected", "reactions_and_listening": "connected",
        "selection_history": "connected", "skip_timing": "connected",
    }
    return response
