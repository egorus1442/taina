from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas import SourceRead, TelegramConnectRequest, TelegramDialogRead
from app.services.telegram_dialogs import (
    connect_dialog_with_linked_chat,
    list_dialogs_from_client,
    temporary_telegram_client,
)


router = APIRouter()


@router.get("/dialogs", response_model=list[TelegramDialogRead])
async def get_telegram_dialogs(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    query: str | None = None,
    include_users: bool = False,
    db: Session = Depends(get_db),
) -> list[TelegramDialogRead]:
    collector = getattr(request.app.state, "collector", None)
    client = getattr(collector, "client", None)

    try:
        if client:
            return await list_dialogs_from_client(
                client=client,
                db=db,
                limit=limit,
                query=query,
                include_users=include_users,
            )

        async with temporary_telegram_client() as temporary_client:
            return await list_dialogs_from_client(
                client=temporary_client,
                db=db,
                limit=limit,
                query=query,
                include_users=include_users,
            )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/connect", response_model=list[SourceRead])
async def post_telegram_connect(
    request: Request,
    payload: TelegramConnectRequest,
    db: Session = Depends(get_db),
) -> list[SourceRead]:
    collector = getattr(request.app.state, "collector", None)
    client = getattr(collector, "client", None)

    try:
        if client:
            return await connect_dialog_with_linked_chat(client=client, db=db, payload=payload)

        async with temporary_telegram_client() as temporary_client:
            return await connect_dialog_with_linked_chat(client=temporary_client, db=db, payload=payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
