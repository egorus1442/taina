import json
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories import (
    get_ai_system_prompt_setting,
    get_keyword_patterns_setting,
    upsert_app_setting,
)
from app.schemas import AdminSettingsRead, AdminSettingsUpdate
from app.services.admin_settings import (
    SETTINGS_KEY_AI_SYSTEM_PROMPT,
    SETTINGS_KEY_KEYWORD_PATTERNS,
    normalize_keyword_patterns,
)


router = APIRouter()


@router.get("", response_model=AdminSettingsRead)
def get_admin_settings(db: Session = Depends(get_db)) -> AdminSettingsRead:
    return AdminSettingsRead(
        keyword_patterns=get_keyword_patterns_setting(db),
        ai_system_prompt=get_ai_system_prompt_setting(db),
    )


@router.put("", response_model=AdminSettingsRead)
def update_admin_settings(
    payload: AdminSettingsUpdate,
    db: Session = Depends(get_db),
) -> AdminSettingsRead:
    if payload.keyword_patterns is not None:
        try:
            keyword_patterns = normalize_keyword_patterns(payload.keyword_patterns)
        except re.error as exc:
            raise HTTPException(status_code=422, detail=f"Некорректный regex: {exc}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        upsert_app_setting(
            db,
            key=SETTINGS_KEY_KEYWORD_PATTERNS,
            value=json.dumps(keyword_patterns, ensure_ascii=False),
        )

    if payload.ai_system_prompt is not None:
        prompt = payload.ai_system_prompt.strip()
        if not prompt:
            raise HTTPException(status_code=422, detail="Prompt не может быть пустым")
        upsert_app_setting(db, key=SETTINGS_KEY_AI_SYSTEM_PROMPT, value=prompt)

    return get_admin_settings(db)
