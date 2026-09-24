"""Primary-only management; temporary login remains disabled until channel guards land."""

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from .. import access_keys
from ..auth import require_primary_session
from .management_http import require_json_same_origin


class PrivateRoute(APIRoute):
    """No caching of credentials/metadata/errors; no validation echo of input values."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private(request: Request):
            try:
                response = await handler(request)
            except RequestValidationError:
                response = JSONResponse({"detail": "Invalid access-key request"}, status_code=422)
            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                        headers=exc.headers)
            response.headers["Cache-Control"] = "no-store"
            return response
        return private


router = APIRouter(prefix="/api/access-keys", tags=["access keys"], route_class=PrivateRoute,
                   dependencies=[Depends(require_primary_session)])


class CreatedKey(BaseModel):
    metadata: access_keys.KeyMetadata
    secret: str = Field(repr=False)
    login_enabled: Literal[False] = False


class KeyPage(BaseModel):
    items: list[access_keys.KeyMetadata]
    limit: int
    offset: int
    login_enabled: Literal[False] = False


class RevokeKey(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.get("", response_model=KeyPage)
def list_keys(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)) -> KeyPage:
    try:
        return KeyPage(items=access_keys.list_keys(limit=limit, offset=offset), limit=limit, offset=offset)
    except (sqlite3.Error, ValueError, OverflowError):
        raise HTTPException(503, "Access-key storage unavailable") from None


@router.post("", status_code=201, response_model=CreatedKey,
             dependencies=[Depends(require_json_same_origin)])
def create_key(body: access_keys.CreateKey) -> CreatedKey:
    try:
        created = access_keys.create(body)
        return CreatedKey(metadata=created.metadata, secret=created.secret)
    except access_keys.InvalidExpiration as exc:
        raise HTTPException(422, str(exc)) from None
    except (sqlite3.Error, ValueError, OverflowError):
        raise HTTPException(503, "Access-key storage unavailable") from None


@router.post("/{key_id}/revoke", response_model=access_keys.KeyMetadata,
             dependencies=[Depends(require_json_same_origin)])
def revoke_key(body: RevokeKey, key_id: str = Path(pattern=r"^[0-9a-f]{32}$")) -> access_keys.KeyMetadata:
    try:
        metadata = access_keys.revoke(key_id)
    except (sqlite3.Error, ValueError, OverflowError):
        raise HTTPException(503, "Access-key storage unavailable") from None
    if metadata is None:
        raise HTTPException(404, "Access key not found")
    return metadata
