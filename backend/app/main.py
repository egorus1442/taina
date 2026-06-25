from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.database import Base, engine
from app.core.schema import ensure_database_schema
from app.services.event_broker import event_broker
from app.services.telegram_collector import TelegramCollector


collector: TelegramCollector | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_database_schema()

    global collector
    app.state.collector = None
    if settings.telegram_enabled:
        collector = TelegramCollector(event_broker=event_broker)
        await collector.start()
        app.state.collector = collector

    try:
        yield
    finally:
        if collector:
            await collector.stop()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_prefix)
