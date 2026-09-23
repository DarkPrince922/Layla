"""Application settings, loaded from environment (see .env.example)."""
from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # --- General ---
    env: str = Field(default="dev", alias="LAYLA_ENV")
    public_host: str = Field(default="localhost", alias="LAYLA_PUBLIC_HOST")
    # E-mail админа, создаваемого при первой установке (если пользователей ещё нет).
    # Домен .app — валидный (в отличие от .local), почта туда не шлётся: это лишь
    # логин. Замените на свой реальный e-mail через LAYLA_ADMIN_EMAIL.
    admin_email: str = Field(default="admin@layla.app", alias="LAYLA_ADMIN_EMAIL")
    # Открытая саморегистрация. По умолчанию ВЫКЛ: пользователей заводит админ.
    # Включите, если хотите разрешить всем регистрироваться самостоятельно.
    allow_open_registration: bool = Field(
        default=False, alias="LAYLA_ALLOW_OPEN_REGISTRATION"
    )

    # --- Crypto / auth ---
    # Fernet key for secrets-at-rest. A dev fallback is generated if unset so
    # the app boots locally; in prod LAYLA_SECRET_KEY MUST be provided.
    secret_key: str = Field(default="", alias="LAYLA_SECRET_KEY")
    jwt_secret: str = Field(default="dev-insecure-jwt-secret", alias="LAYLA_JWT_SECRET")
    jwt_ttl_minutes: int = Field(default=1440, alias="LAYLA_JWT_TTL_MINUTES")

    # --- Database ---
    # DATABASE_URL можно задать явно (напр. внешняя БД). Если он пуст — собирается
    # из POSTGRES_* ниже, чтобы смена пароля не рассинхронизировалась с URL.
    database_url: str = Field(default="", alias="DATABASE_URL")
    postgres_user: str = Field(default="layla", alias="POSTGRES_USER")
    postgres_password: str = Field(default="layla", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="layla", alias="POSTGRES_DB")
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")

    @model_validator(mode="after")
    def _assemble_database_url(self) -> "Settings":
        """Собрать DATABASE_URL из частей, если он не задан явно.

        Логин/пароль URL-кодируются — иначе символы @ / : в пароле ломают строку
        подключения. Явный DATABASE_URL (в т.ч. sqlite в тестах) имеет приоритет.
        """
        if not self.database_url:
            user = quote(self.postgres_user, safe="")
            password = quote(self.postgres_password, safe="")
            self.database_url = (
                f"postgresql+asyncpg://{user}:{password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return self

    # --- Redis ---
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # Куда клонируются репозитории / создаются проекты (Settings -> General).
    projects_dir: str = Field(default="./data/projects", alias="LAYLA_PROJECTS_DIR")

    # Адреса анонимизирующих маршрутов для egress-проб (спец. §7.4).
    tor_addr: str = Field(default="127.0.0.1:9050", alias="LAYLA_TOR_ADDR")
    proxy_addr: str = Field(default="", alias="LAYLA_PROXY_ADDR")

    # --- Запуск кода ---
    # Песочница проекта (свой сервис sandbox/): команды с зависимостями в рабочей копии.
    # unix:///путь/к/сокету или http://хост:порт. Пусто — запуск команд недоступен.
    sandbox_url: str = Field(default="unix:///run/layla-sandbox/sandbox.sock", alias="LAYLA_SANDBOX_URL")
    # Piston: компиляция и запуск программ на десятках языков без зависимостей.
    piston_url: str = Field(default="http://piston:2000", alias="LAYLA_PISTON_URL")
    # Превью приложений открывается на отдельном адресе (другой порт Caddy): так код превью
    # не работает от имени Лейлы. LAYLA_PREVIEW_URL — полный адрес, если он отличается от
    # «та же схема и хост, порт LAYLA_PREVIEW_PORT».
    preview_port: int = Field(default=8090, alias="LAYLA_PREVIEW_PORT")
    preview_url: str = Field(default="", alias="LAYLA_PREVIEW_URL")

    # --- LiteLLM ---
    litellm_base_url: str = Field(default="http://localhost:4000", alias="LITELLM_BASE_URL")
    litellm_master_key: str = Field(default="", alias="LITELLM_MASTER_KEY")

    @property
    def is_prod(self) -> bool:
        return self.env.lower() in {"prod", "production"}

    def validate_for_prod(self) -> list[str]:
        """Проверки безопасности для прод-режима (спец. §7.5, §7.6)."""
        problems: list[str] = []
        if not self.is_prod:
            return problems
        if not self.secret_key:
            problems.append("LAYLA_SECRET_KEY обязателен в prod (шифрование секретов)")
        if not self.jwt_secret or self.jwt_secret == "dev-insecure-jwt-secret":
            problems.append("LAYLA_JWT_SECRET должен быть задан и не равен dev-значению")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
