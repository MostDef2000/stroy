from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
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

    redis_url: str = ""
    redis_job_channel: str = "stroy:jobs"

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
    worker_token_hashes: str = ""
    worker_lease_seconds: int = 120
    worker_heartbeat_grace_seconds: int = 60

    model_profiles_path: str = "config/model-profiles.json"
    model_use: str = "personal-non-commercial"
    llm_base_url: str = "http://127.0.0.1:8001/v1"
    llm_api_key: str = "local"
    llm_model_profile: str = "qwen3-14b"
    comfyui_url: str = "http://127.0.0.1:8188"
    image_model_profile: str = "flux-dev-family"
    blender_bin: str = "blender"

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> Settings:
        if self.env != "production":
            return self
        fields = type(self).model_fields
        default_session_secret = fields["session_secret"].default
        if not self.session_secret or self.session_secret == default_session_secret:
            raise ValueError("production requires a secure STROY_SESSION_SECRET")
        default_s3_secret = fields["s3_secret_key"].default
        if self.storage_backend == "s3" and (
            not self.s3_secret_key or self.s3_secret_key == default_s3_secret
        ):
            raise ValueError(
                "production s3 storage requires a secure STROY_S3_SECRET_KEY"
            )
        default_worker_token = fields["worker_token"].default
        if (
            self.worker_token_hashes == ""
            and self.worker_token_hash == ""
            and self.worker_token == default_worker_token
        ):
            raise ValueError(
                "production requires STROY_WORKER_TOKEN_HASHES or "
                "STROY_WORKER_TOKEN_HASH (or a non-default STROY_WORKER_TOKEN)"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
