"""Runtime settings. All values come from environment variables (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LQ_", env_file=".env", extra="ignore")

    env: str = "dev"  # dev | test | prod
    database_url: str = "postgresql+psycopg://lq:lq@127.0.0.1:55432/lq"

    # storage: "local" (filesystem) or "s3" (S3-compatible, MinIO in dev)
    storage_backend: str = "local"
    storage_local_dir: Path = REPO_ROOT / "var" / "storage"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "listening-quest"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"

    work_dir: Path = REPO_ROOT / "var" / "work"
    outbox_dir: Path = REPO_ROOT / "var" / "outbox"
    fixtures_dir: Path = REPO_ROOT / "fixtures"

    # auth
    cookie_secure: bool = False  # must be true behind HTTPS (prod)
    session_ttl_hours: int = 24 * 30
    login_max_failures: int = 5
    login_window_minutes: int = 15
    public_origin: str | None = None  # e.g. https://lq.example.com ; enables Origin check when set
    import_token: str | None = None  # bearer token for CLI -> import API

    # generator CLI -> server connection (optional). When set, `lq generate` auto-registers.
    server_url: str | None = None

    # providers
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_base_url: str = "https://api.openai.com/v1"
    openai_realtime_url: str = "wss://api.openai.com/v1/realtime"
    content_model: str = "gpt-6.1-terra"
    review_model: str = "gpt-6.1-terra"
    tts_provider: str = "openai_realtime"  # openai_realtime | openai_speech (deprecated, shuts down 2027-01-06)
    tts_model: str = "gpt-realtime-2.1-mini"
    tts_speech_model: str = "gpt-4o-mini-tts"
    asr_model: str = "gpt-transcribe"
    tts_narrator_voice: str = "cedar"
    tts_ja_voice: str = "marin"
    tts_speaker_voices: str = "alloy,ash,coral,sage,echo,shimmer,verse,ballad"
    provider_timeout_sec: float = 120.0

    # worker
    worker_poll_sec: float = 2.0
    job_lease_sec: int = 120
    job_max_attempts: int = 3
    job_retry_base_sec: int = 30
    job_retry_max_sec: int = 600
    replenish_interval_sec: int = 300


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
