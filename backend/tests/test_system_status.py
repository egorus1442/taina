from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes.system import get_system_status
from app.core.config import settings
from app.core.database import Base


def test_system_status_works_without_telegram_collector(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(collector=None)),
    )
    monkeypatch.setattr(settings, "telegram_enabled", False)

    try:
        status = get_system_status(request=request, db=db)
    finally:
        db.close()

    assert status.status == "disabled"
    assert status.telegram.connected is False
    assert status.enabled_sources == 0
    assert status.disk_free_gb > 0
