from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.fixture(autouse=True)
def clear_settings_env(monkeypatch):
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.delenv("APP_DEBUG", raising=False)


def test_defaults_without_env_file():
    settings = Settings(_env_file=None)

    assert settings.app_name == "Заявки в жилищную службу"
    assert settings.app_debug is False


def test_reads_dotenv_and_ignores_extra_variables(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_NAME=Тестовый дом\nAPP_DEBUG=true\nOTHER_SETTING=value\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.app_name == "Тестовый дом"
    assert settings.app_debug is True


def test_environment_overrides_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("APP_NAME=Из файла\nAPP_DEBUG=false\n", encoding="utf-8")
    monkeypatch.setenv("APP_NAME", "Из окружения")
    monkeypatch.setenv("APP_DEBUG", "true")

    settings = Settings(_env_file=env_file)

    assert settings.app_name == "Из окружения"
    assert settings.app_debug is True


def test_invalid_debug_is_rejected(monkeypatch):
    monkeypatch.setenv("APP_DEBUG", "неизвестно")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("name", ["", "   "])
def test_empty_name_is_rejected(monkeypatch, name):
    monkeypatch.setenv("APP_NAME", name)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_default_env_path_is_absolute_and_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert Settings.model_config["env_file"] == (
        Path(__file__).resolve().parent.parent / ".env"
    )
