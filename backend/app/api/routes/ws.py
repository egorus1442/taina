from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.event_broker import event_broker


router = APIRouter()


@router.websocket("/events")
async def events_ws(websocket: WebSocket) -> None:
    await event_broker.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await event_broker.disconnect(websocket)

