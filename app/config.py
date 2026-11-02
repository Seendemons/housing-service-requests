from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Заявки в жилищную службу"
    app_debug: bool = False
    session_cookie_secure: bool = False

    @field_validator("app_name")
    @classmethod
    def name_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("APP_NAME не может быть пустым")
        return value


class DatabaseSettings(BaseSettings):
    """Настройки БД проверяются только при обращении к ней."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "housing_requests"
    postgres_user: str = "housing_user"
    postgres_password: SecretStr = SecretStr("")

    @field_validator("postgres_host", "postgres_db", "postgres_user")
    @classmethod
    def database_text_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Настройка подключения к БД не может быть пустой")
        return value

    @field_validator("postgres_port")
    @classmethod
    def port_must_be_valid(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("Порт БД должен быть от 1 до 65535")
        return value


class DemoSettings(BaseSettings):
    """Пароли требуются только при явном создании демо-пользователей."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )
    demo_resident_password: SecretStr = SecretStr("")
    demo_dispatcher_password: SecretStr = SecretStr("")
