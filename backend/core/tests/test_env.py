"""关键凭证环境变量加载行为。"""
from pathlib import Path

import pytest

from backend.core.config import Settings
from backend.core.env import (
    TIGER_CREDENTIAL_ENV_KEYS,
    load_project_environment,
    normalized_optional_env,
)


@pytest.fixture
def tiger_dotenv(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        "TIGER_ID=dotenv-id\n"
        "TIGER_ACCOUNT=dotenv-account\n"
        "TIGER_PRIVATE_KEY_PATH=keys/tiger.pem\n",
        encoding="utf-8",
    )
    return path


def test_missing_environment_variables_load_from_dotenv(monkeypatch, tiger_dotenv):
    for key in TIGER_CREDENTIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    load_project_environment(tiger_dotenv)

    assert Settings().tiger_credentials_loaded is True


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_environment_values_do_not_mask_dotenv(monkeypatch, tiger_dotenv, blank):
    for key in TIGER_CREDENTIAL_ENV_KEYS:
        monkeypatch.setenv(key, blank)

    load_project_environment(tiger_dotenv)

    settings = Settings()
    assert settings.tiger_credentials_loaded is True
    assert settings.tiger_id == "dotenv-id"


def test_valid_environment_value_takes_precedence_over_dotenv(monkeypatch, tiger_dotenv):
    monkeypatch.setenv("TIGER_ID", "runtime-id")
    monkeypatch.setenv("TIGER_ACCOUNT", "runtime-account")
    monkeypatch.setenv("TIGER_PRIVATE_KEY_PATH", "runtime/key.pem")

    load_project_environment(tiger_dotenv)

    settings = Settings()
    assert settings.tiger_id == "runtime-id"
    assert settings.tiger_account == "runtime-account"
    assert settings.tiger_private_key_path == "runtime/key.pem"


@pytest.mark.parametrize("value", ["", " \t "])
def test_optional_config_normalizes_blank_values_to_none(monkeypatch, value):
    monkeypatch.setenv("TIGER_ID", value)
    assert normalized_optional_env("TIGER_ID") is None
