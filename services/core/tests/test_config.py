"""The repository .env is found from any working directory, and secrets are never printed."""

import json
import os
import subprocess
import sys
from pathlib import Path

from lq import config

CORE = Path(__file__).resolve().parents[1]


def test_default_env_file_is_repository_root_independent_of_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("LQ_ENV_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    assert config.env_file_path() == config.REPO_ROOT / ".env"
    monkeypatch.setenv("LQ_ENV_FILE", "none")
    assert config.env_file_path() is None


def test_env_file_is_read_by_cli_from_another_directory_and_secrets_stay_hidden(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=sk-test-should-never-be-printed   # comment\n"
        "LQ_IMPORT_TOKEN=import-secret-value\n"
        "LQ_TTS_MODEL=gpt-realtime-2.1-mini\n"
        "LQ_CONTENT_MODEL=model-from-dotenv\n"
        "LQ_DATABASE_URL=postgresql+psycopg://lq:db-password-here@127.0.0.1:55432/lq\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("LQ_") and k != "OPENAI_API_KEY"}
    env["LQ_ENV_FILE"] = str(env_file)
    env["LQ_CONTENT_MODEL_UNUSED"] = "x"
    out = subprocess.run(
        [sys.executable, "-m", "lq.cli", "config"],
        cwd=tmp_path,
        env={**env, "PYTHONPATH": str(CORE)},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for secret in ("sk-test-should-never-be-printed", "import-secret-value", "db-password-here"):
        assert secret not in out
    data = json.loads(out)
    assert data["env_file_found"] is True
    assert data["openai_api_key"] == "set" and data["import_token"] == "set"
    assert data["content_model"] == "model-from-dotenv"
    assert data["database_url"].startswith("postgresql+psycopg://lq:***@")


def test_environment_variables_win_over_dotenv(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("LQ_CONTENT_MODEL=from-file\nLQ_REVIEW_MODEL=from-file\n")
    monkeypatch.setenv("LQ_ENV_FILE", str(env_file))
    monkeypatch.setenv("LQ_CONTENT_MODEL", "from-env")
    config.reset_settings_cache()
    try:
        s = config.get_settings()
        assert (s.content_model, s.review_model) == ("from-env", "from-file")
    finally:
        monkeypatch.setenv("LQ_ENV_FILE", "none")
        config.reset_settings_cache()


def test_platform_database_url_and_domain_are_used_when_ours_are_missing(monkeypatch):
    monkeypatch.setenv("LQ_ENV_FILE", "none")
    monkeypatch.delenv("LQ_DATABASE_URL", raising=False)
    monkeypatch.delenv("LQ_PUBLIC_ORIGIN", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.internal:5432/railway")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "lq-production.up.railway.app")
    config.reset_settings_cache()
    try:
        s = config.get_settings()
        assert s.database_url == "postgresql+psycopg://u:p@db.internal:5432/railway"
        assert s.public_origin == "https://lq-production.up.railway.app"
        monkeypatch.setenv("LQ_DATABASE_URL", "postgresql+psycopg://lq:lq@127.0.0.1:55432/lq_test")  # ours win
        config.reset_settings_cache()
        assert config.get_settings().database_url.endswith("/lq_test")
    finally:
        config.reset_settings_cache()
