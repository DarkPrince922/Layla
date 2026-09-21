from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "layla-core"}


@router.get("/meta")
async def meta() -> dict:
    """Runtime info the frontend uses (e.g. to decide the HTTP key-entry banner)."""
    settings = get_settings()
    return {
        "env": settings.env,
        "is_prod": settings.is_prod,
        "domains": ["code", "pentest", "osint", "design"],
        # Frontend also checks window.isSecureContext; this is the server hint.
        "https_required": settings.is_prod,
    }
