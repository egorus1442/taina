from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Telegram Drone Safety Monitor"
    api_prefix: str = "/api"
    debug: bool = False

    data_dir: Path = Path("./data")
    database_url: str = "sqlite:///./data/app.db"

    cors_origins_csv: str = Field(
        default="http://localhost:5173",
        validation_alias="CORS_ORIGINS",
    )

    telegram_enabled: bool = False
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session_name: str = "drone_monitor"
    telegram_poll_enabled_sources: bool = True
    telegram_poll_interval_seconds: float = 60.0
    telegram_poll_limit_per_source: int = 20

    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "anthropic/claude-haiku-4.5"
    openrouter_timeout_seconds: float = 30.0

    geocoder_provider: str = "nominatim"
    yandex_geocoder_api_key: str | None = None
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    nominatim_user_agent: str = "telegram-drone-safety-monitor/0.1"
    photon_base_url: str = "https://photon.komoot.io"

    webhook_urls_csv: str = Field(default="", validation_alias="WEBHOOK_URLS")
    webhook_timeout_seconds: float = 5.0

    @property
    def cors_origins(self) -> list[str]:
        return self._parse_csv_list(self.cors_origins_csv)

    @property
    def webhook_urls(self) -> list[str]:
        return self._parse_csv_list(self.webhook_urls_csv)

    def _parse_csv_list(self, value: str | None) -> list[str]:
        if not value:
            return []
        return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    value = Settings()
    value.data_dir.mkdir(parents=True, exist_ok=True)
    return value


settings = get_settings()
