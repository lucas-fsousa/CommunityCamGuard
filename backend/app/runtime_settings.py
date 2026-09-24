"""Explicit two-field runtime settings contract; never serializes bootstrap Settings."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from .config import get_settings
from .db import runtime_settings as store

GridLimit = Annotated[StrictInt, Field(ge=0, le=64)]
CacheLimit = Annotated[StrictInt, Field(ge=0, le=65536)]


class Overrides(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grid_hd_max_cameras: GridLimit | None = None
    playback_cache_mb: CacheLimit | None = None


class Changes(Overrides):
    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set:
            raise ValueError("at least one setting is required")
        return self


class Update(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: Annotated[StrictInt, Field(ge=0)]
    changes: Changes


def _snapshot(revision: int, raw: dict) -> dict:
    overrides = Overrides.model_validate(raw).model_dump(exclude_none=True)
    baseline = get_settings()
    values = {
        "grid_hd_max_cameras": baseline.grid_hd_max_cameras,
        "playback_cache_mb": baseline.playback_cache_mb,
    }
    values.update(overrides)
    return {"revision": revision, "values": values, "overrides": overrides,
            "application": {"grid_hd_max_cameras": "next_media_metadata_fetch",
                            "playback_cache_mb": "next_cache_policy_check"}}


def snapshot() -> dict:
    return _snapshot(*store.read())


def update(patch: Update) -> dict:
    # Validate stored overrides before merging as well as validating incoming names/types.
    snapshot()
    return _snapshot(*store.write(patch.revision, patch.changes.model_dump(exclude_unset=True)))


def grid_hd_limit() -> int:
    return int(snapshot()["values"]["grid_hd_max_cameras"])


def playback_cache_limit_mb() -> int:
    return int(snapshot()["values"]["playback_cache_mb"])
