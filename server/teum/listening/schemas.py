"""기기 등록·청취 이벤트·관심사 요청 형식."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator, model_validator

EventType = Literal["play", "position", "pause", "resume", "skip", "complete", "duck_start", "duck_end",
                    "like", "unlike", "save", "unsave", "ad_start", "ad_complete", "ad_error"]
# 넘긴 이유. user_next(다음 버튼·스티어링 키)만 "듣다 넘김" 신호로 쓰고 나머지는 중립이다.
SkipReason = Literal["user_next", "user_prev", "auto", "arrival", "error"]


class EventIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)  # 모르는 필드(예: 예전 user_id)는 무시한다

    event_id: str
    content_id: str
    content_version: int = 1
    playback_id: str | None = None
    seq: int
    type: EventType
    position_sec: float | None = None
    playback_rate: float = 1.0
    client_ts: AwareDatetime
    skip_reason: SkipReason | None = None   # type=skip일 때만 의미가 있다
    selection_id: str | None = None         # 골라 듣기에서 시작한 재생이면 POST /selections의 id

    @field_validator("event_id")
    @classmethod
    def uuid_id(cls, value):
        return str(UUID(value))

    @field_validator("selection_id")
    @classmethod
    def optional_uuid(cls, value):
        return None if value is None else str(UUID(value))

    @field_validator("position_sec", "seq")
    @classmethod
    def non_negative(cls, value):
        if value is not None and value < 0:
            raise ValueError("must be non-negative")
        return value

    @field_validator("playback_rate")
    @classmethod
    def valid_playback_rate(cls, value):
        if not 0 < value <= 3:
            raise ValueError("playback_rate must be greater than 0 and at most 3")
        return value

    @model_validator(mode="after")
    def playback_fields(self):
        if self.type in {"play", "position", "pause", "resume", "skip", "complete", "duck_start", "duck_end"}:
            if not self.playback_id:
                raise ValueError("playback_id is required for playback events")
            if self.position_sec is None:
                raise ValueError("position_sec is required for playback events")
        return self

    @field_validator("client_ts", mode="before")
    @classmethod
    def iso_timestamp(cls, value):
        if not isinstance(value, str):
            raise ValueError("client_ts must be ISO 8601 text")
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class DeviceIn(BaseModel):
    install_id: str
    app_version: str
    platform: str


class BatchIn(BaseModel):
    events: list[Any]


class SelectionIn(BaseModel):
    """골라 듣기에서 주제를 고른 기록. 재생이 실제로 시작돼야(같은 selection_id의 play) 선호에 반영된다."""
    selection_id: str
    interests: list[str]
    selected_at: AwareDatetime

    @field_validator("selection_id")
    @classmethod
    def uuid_id(cls, value):
        return str(UUID(value))

    @field_validator("interests")
    @classmethod
    def some_interests(cls, value):
        value = list(dict.fromkeys(v.strip() for v in value if v.strip()))
        if not value or len(value) > 21:
            raise ValueError("interests must have 1 to 21 codes")
        return value


class InterestsIn(BaseModel):
    interests: list[str]
