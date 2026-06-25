from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories import (
    create_source_group,
    create_source,
    get_source,
    get_source_group,
    get_source_by_telegram_id,
    list_source_groups,
    list_sources,
    update_source_group,
    update_source,
)
from app.schemas import SourceCreate, SourceGroupCreate, SourceGroupRead, SourceGroupUpdate, SourceRead, SourceUpdate


router = APIRouter()


@router.get("", response_model=list[SourceRead])
def get_sources(db: Session = Depends(get_db)) -> list[SourceRead]:
    return list_sources(db)


@router.post("", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
def post_source(payload: SourceCreate, db: Session = Depends(get_db)) -> SourceRead:
    if get_source_by_telegram_id(db, payload.telegram_id):
        raise HTTPException(status_code=409, detail="Source already exists")
    return create_source(db, payload)


@router.get("/groups", response_model=list[SourceGroupRead])
def get_groups(db: Session = Depends(get_db)) -> list[SourceGroupRead]:
    return list_source_groups(db)


@router.post("/groups", response_model=SourceGroupRead, status_code=status.HTTP_201_CREATED)
def post_group(payload: SourceGroupCreate, db: Session = Depends(get_db)) -> SourceGroupRead:
    return create_source_group(db, payload)


@router.patch("/groups/{group_id}", response_model=SourceGroupRead)
def patch_group(group_id: int, payload: SourceGroupUpdate, db: Session = Depends(get_db)) -> SourceGroupRead:
    group = get_source_group(db, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Source group not found")
    return update_source_group(db, group, payload)


@router.patch("/{source_id}", response_model=SourceRead)
def patch_source(source_id: int, payload: SourceUpdate, db: Session = Depends(get_db)) -> SourceRead:
    source = get_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return update_source(db, source, payload)
