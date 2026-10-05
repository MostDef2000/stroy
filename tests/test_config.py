from __future__ import annotations

import os
from uuid import uuid4

import pytest
from pydantic import ValidationError

from stroy.config import Settings
from stroy.security import sha256_text, verify_worker_token


def _runtime_value(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _default(field: str) -> str:
    return Settings.model_fields[field].default


def _session_secret_field() -> str:
    return "session" + "_secret"


@pytest.fixture(autouse=True)
def _clear_stroy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("STROY_"):
            monkeypatch.delenv(name, raising=False)


def production_settings(**updates) -> Settings:
    """Valid production baseline: custom session secret and worker token."""
    values = {
        "env": "production",
        "session_secret": _runtime_value("session"),
        "worker_token": _runtime_value("worker"),
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def test_production_without_session_secret_is_rejected():
    with pytest.raises(ValidationError, match="STROY_SESSION_SECRET"):
        Settings(
            _env_file=None,
            env="production",
            worker_token=_runtime_value("worker"),
        )


def test_production_with_development_session_secret_is_rejected():
    field = _session_secret_field()
    with pytest.raises(ValidationError, match="STROY_SESSION_SECRET"):
        production_settings(**{field: _default(field)})


def test_production_with_custom_session_secret_is_accepted():
    settings = production_settings()
    assert settings.env == "production"
    assert settings.storage_backend == "memory"


def test_production_s3_with_development_secret_key_is_rejected():
    with pytest.raises(ValidationError, match="STROY_S3_SECRET_KEY"):
        production_settings(storage_backend="s3")


def test_production_s3_with_empty_secret_key_is_rejected():
    with pytest.raises(ValidationError, match="STROY_S3_SECRET_KEY"):
        production_settings(storage_backend="s3", s3_secret_key="")


def test_production_s3_with_custom_secret_key_is_accepted():
    settings = production_settings(
        storage_backend="s3",
        s3_secret_key=_runtime_value("s3"),
    )
    assert settings.storage_backend == "s3"


def test_production_worker_fallback_to_development_token_is_rejected():
    with pytest.raises(ValidationError, match="STROY_WORKER_TOKEN"):
        production_settings(
            worker_token=_default("worker_token"),
            worker_token_hash="",
            worker_token_hashes="",
        )


def test_production_worker_hash_disables_fallback_check():
    settings = production_settings(
        worker_token=_default("worker_token"),
        worker_token_hash=sha256_text(_runtime_value("worker")),
    )
    assert settings.worker_token_hash


def test_production_worker_hashes_do_not_disable_raw_fallback():
    with pytest.raises(ValidationError, match="STROY_WORKER_TOKEN_HASH"):
        production_settings(
            worker_token=_default("worker_token"),
            worker_token_hash="",
            worker_token_hashes=sha256_text(_runtime_value("worker")),
        )


def test_production_empty_worker_token_is_rejected():
    with pytest.raises(ValidationError, match="STROY_WORKER_TOKEN_HASH"):
        production_settings(
            worker_token="",
            worker_token_hash="",
            worker_token_hashes="",
        )


def test_production_documented_list_only_config_is_accepted():
    runtime_token = _runtime_value("worker")
    settings = production_settings(
        worker_token="",
        worker_token_hash="",
        worker_token_hashes=sha256_text(runtime_token),
    )
    assert settings.env == "production"
    assert settings.worker_token == ""
    assert settings.worker_token_hash == ""
    assert settings.worker_token_hashes == sha256_text(runtime_token)
    # Raw-token path is dead: neither the runtime token nor the development
    # default authenticate through the (empty) single-token configuration.
    assert (
        verify_worker_token(
            runtime_token, settings.worker_token, settings.worker_token_hash
        )
        is False
    )
    assert (
        verify_worker_token(
            _default("worker_token"),
            settings.worker_token,
            settings.worker_token_hash,
        )
        is False
    )
    # List hashes authenticate independently and are additive.
    assert (
        verify_worker_token(runtime_token, "", settings.worker_token_hashes) is True
    )


def test_production_junk_list_only_credentials_rejected():
    with pytest.raises(ValidationError, match="STROY_WORKER_TOKEN_HASHES"):
        production_settings(
            worker_token="",
            worker_token_hash="",
            worker_token_hashes=" , ,  ",
        )


def test_production_whitespace_single_hash_with_default_token_rejected():
    with pytest.raises(ValidationError, match="STROY_WORKER_TOKEN_HASH"):
        production_settings(
            worker_token=_default("worker_token"),
            worker_token_hash="   ",
        )


@pytest.mark.parametrize("env", ["PRODUCTION", "production "])
def test_production_env_is_normalized(env):
    with pytest.raises(ValidationError, match="STROY_SESSION_SECRET"):
        Settings(
            _env_file=None,
            env=env,
            worker_token=_runtime_value("worker"),
        )


def test_production_whitespace_session_secret_is_rejected():
    with pytest.raises(ValidationError, match="STROY_SESSION_SECRET"):
        Settings(
            _env_file=None,
            env="production",
            session_secret="   ",
            worker_token=_runtime_value("worker"),
        )


def test_production_s3_whitespace_secret_key_is_rejected():
    with pytest.raises(ValidationError, match="STROY_S3_SECRET_KEY"):
        production_settings(storage_backend="s3", s3_secret_key="  ")


def test_validation_error_does_not_echo_secret_values():
    marker = _runtime_value("marker")
    with pytest.raises(ValidationError) as excinfo:
        production_settings(
            session_secret=marker,
            storage_backend="s3",
            s3_secret_key=_default("s3_secret_key"),
        )
    assert marker not in str(excinfo.value)


def test_development_defaults_are_not_validated():
    settings = Settings(_env_file=None)
    assert settings.env == "development"
