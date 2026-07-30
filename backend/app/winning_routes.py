"""HTTP surface for Winning Products (app/winning.py).

Two endpoints, split by what they cost. Reading the page is free and hits the
fixture plus the local snapshot database; `live=true` is the only thing that
spends Rainforest credits, so it has to be asked for explicitly rather than
happening on every page load.
"""
from fastapi import APIRouter, HTTPException, Query

from . import winning
from .winning import WinningResponse

router = APIRouter(prefix="/api/winning", tags=["winning"])


@router.get("/products", response_model=WinningResponse)
async def products(
    category: str = Query("kitchen", description="Amazon category id, e.g. 'kitchen'"),
    live: bool = Query(
        False,
        description=(
            "Spend Rainforest credits on a fresh chart scan (2 per category) and "
            "record it as a snapshot. Off by default: the page reads the captured "
            "fixture plus local history, which costs nothing."
        ),
    ),
) -> WinningResponse:
    if category not in winning.CATEGORIES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown category '{category}'. Configured: {list(winning.CATEGORIES)}",
        )
    if live and not winning.settings.rainforest_api_key:
        raise HTTPException(status_code=400, detail="RAINFOREST_API_KEY is not set.")
    return await winning.winning_products(category=category, live=live)


@router.get("/categories")
async def categories() -> list[dict]:
    return [{"id": k, "label": v["label"]} for k, v in winning.CATEGORIES.items()]
