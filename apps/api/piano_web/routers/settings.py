"""/api/settings — full runtime settings, GUI-editable.

GET  /api/settings        — entire merged settings dict (deep merge over defaults)
POST /api/settings        — partial update; deep-merged into the persisted copy
GET  /api/settings/schema — advertises the default shape so the GUI can render

YAML is the canonical persistence format (see piano_web.settings). Endpoints
always speak JSON.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from piano_web import settings as settings_module


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    # Accept any nested dict — we validate leaves in the settings module rather
    # than at the HTTP boundary so clients can extend the schema without
    # triggering 422s on unknown keys.
    model_config = {"extra": "allow"}


@router.get("")
async def get_full_settings() -> dict[str, Any]:
    return settings_module.load()


@router.post("")
async def update_full_settings(body: dict[str, Any]) -> dict[str, Any]:
    merged = settings_module.save(body)
    logger.info("api.settings.save", extra={"updated_keys": list(body)})
    return merged


@router.get("/schema")
async def settings_schema() -> dict[str, Any]:
    """Return the default settings shape — useful for GUI form generation."""
    return settings_module.DEFAULT_SETTINGS
