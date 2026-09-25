from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="STROY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "development"
    log_level: str = "INFO"
    public_base_url: str = "http://localhost:8000"
    trusted_hosts: str = "localhost,127.0.0.1,test"

    database_url: str = "sqlite+aiosqlite:///./stroy.db"
    auto_create_schema: bool = True

    storage_backend: str = "memory"
    max_upload_bytes: int = 100 * 1024 * 1024
    upload_allowed_media_types: str = (
        "image/*,video/*,model/*,application/pdf,application/json,"
        "application/octet-stream,text/plain"
    )
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "stroy"
    s3_secret_key: str = "stroy-development-only"
    s3_bucket: str = "stroy"
    s3_region: str = "local"

    auth_mode: str = "owner-password"
    auth_username: str = "owner"
    auth_password_hash: str = ""
    session_secret: str = "development-only-change-me"
    session_cookie_secure: bool = False
    session_cookie_samesite: str = "lax"
    session_max_age_seconds: int = 604800

    worker_token: str = "development-worker-token"
    worker_token_hash: str = ""
    worker_lease_seconds: int = 120
    worker_heartbeat_grace_seconds: int = 60

    llm_base_url: str = "http://127.0.0.1:8001/v1"
    llm_api_key: str = "local"
    llm_model: str = "Qwen/Qwen3-14B"
    comfyui_url: str = "http://127.0.0.1:8188"
    image_model_profile: str = "flux-dev-family"
    blender_bin: str = "blender"


@lru_cache
def get_settings() -> Settings:
    return Settings()
