from fastapi import APIRouter

from app.api.routes import (
    clusters,
    events,
    geo,
    health,
    ingest,
    location_reviews,
    messages,
    settings,
    sources,
    system,
    telegram,
    ws,
)


api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(sources.router, prefix="/sources", tags=["sources"])
api_router.include_router(messages.router, prefix="/messages", tags=["messages"])
api_router.include_router(events.router, prefix="/events", tags=["events"])
api_router.include_router(geo.router, prefix="/geo", tags=["geo"])
api_router.include_router(clusters.router, prefix="/clusters", tags=["clusters"])
api_router.include_router(location_reviews.router, prefix="/location-reviews", tags=["location-reviews"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
api_router.include_router(system.router, prefix="/system", tags=["system"])
api_router.include_router(ingest.router, prefix="/ingest", tags=["ingest"])
api_router.include_router(telegram.router, prefix="/telegram", tags=["telegram"])
api_router.include_router(ws.router, prefix="/ws", tags=["websocket"])
