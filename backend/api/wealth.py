"""HTTP boundary for Wealth Overview v0.1."""
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app import state as _state
from backend.services import wealth_service as svc

router = APIRouter()


def _pid() -> int:
    return _state.portfolio_id


class WealthItemWrite(BaseModel):
    kind: Literal["asset", "liability"]
    name: str = Field(min_length=1, max_length=200)
    item_type: str
    current_value: float = Field(ge=0)
    value_as_of: date | None = None
    included_in_net_worth: bool | None = None
    already_investment_accounted: bool = False
    source_type: Literal["MANUAL", "PDF", "SCREENSHOT"] = "MANUAL"
    notes: str | None = Field(default=None, max_length=2000)


class WealthItemPatch(BaseModel):
    kind: Literal["asset", "liability"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    item_type: str | None = None
    current_value: float | None = Field(default=None, ge=0)
    value_as_of: date | None = None
    included_in_net_worth: bool | None = None
    already_investment_accounted: bool | None = None
    source_type: Literal["MANUAL", "PDF", "SCREENSHOT"] | None = None
    notes: str | None = Field(default=None, max_length=2000)


@router.get("/summary")
def get_summary(days: int | None = Query(default=None, ge=1, le=7300)):
    return svc.get_summary(_pid(), trend_days=days)


@router.get("/items")
def get_items(kind: Literal["asset", "liability"] | None = Query(default=None)):
    items = svc.list_items(_pid(), kind)
    return {"items": items, "total": len(items)}


@router.post("/items", status_code=201)
def create_item(payload: WealthItemWrite):
    try:
        return svc.create_item(_pid(), payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/items/{item_id}")
def update_item(item_id: int, payload: WealthItemPatch):
    try:
        return svc.update_item(_pid(), item_id, payload.model_dump(exclude_unset=True))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int):
    try:
        svc.delete_item(_pid(), item_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/import/preview")
async def preview_import(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="上传文件为空")
    # Preview deliberately does not persist the original file or a wealth fact.
    return svc.preview_import(file.filename or "未命名文件", content, file.content_type)
