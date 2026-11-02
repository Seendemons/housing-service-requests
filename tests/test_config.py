from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.fixture(autouse=True)
def clear_settings_env(monkeypatch):
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.delenv("APP_DEBUG", raising=False)
    for name in (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)


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


def test_database_settings_dotenv_and_environment_priority(tmp_path, monkeypatch):
    from app.config import DatabaseSettings

    for name in (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_HOST=from-file\nPOSTGRES_PORT=5433\n"
        "POSTGRES_DB=testdb\nPOSTGRES_USER=testuser\nPOSTGRES_PASSWORD=example\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("POSTGRES_HOST", "from-environment")

    settings = DatabaseSettings(_env_file=env_file)

    assert settings.postgres_host == "from-environment"
    assert settings.postgres_port == 5433
    assert settings.postgres_db == "testdb"
    assert settings.postgres_user == "testuser"
    assert settings.postgres_password.get_secret_value() == "example"
    assert "example" not in repr(settings)


def test_database_settings_are_not_checked_by_app_settings(monkeypatch):
    monkeypatch.setenv("POSTGRES_PORT", "invalid")
    assert Settings(_env_file=None).app_debug is False


@pytest.mark.parametrize("port", [0, 65536, "invalid"])
def test_invalid_database_port_is_rejected_without_echoing_input(port):
    from app.config import DatabaseSettings

    with pytest.raises(ValidationError) as error:
        DatabaseSettings(_env_file=None, postgres_port=port)
    assert "input_value" not in str(error.value)
    assert "invalid" not in str(error.value)


@pytest.mark.parametrize("field", ["postgres_host", "postgres_db", "postgres_user"])
@pytest.mark.parametrize("value", ["", "   "])
def test_empty_database_fields_are_rejected(field, value):
    from app.config import DatabaseSettings

    with pytest.raises(ValidationError):
        DatabaseSettings(_env_file=None, **{field: value})
