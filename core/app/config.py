"""Application settings, loaded from environment (see .env.example)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # --- General ---
    env: str = Field(default="dev", alias="LAYLA_ENV")
    public_host: str = Field(default="localhost", alias="LAYLA_PUBLIC_HOST")

    # --- Crypto / auth ---
    # Fernet key for secrets-at-rest. A dev fallback is generated if unset so
    # the app boots locally; in prod LAYLA_SECRET_KEY MUST be provided.
    secret_key: str = Field(default="", alias="LAYLA_SECRET_KEY")
    jwt_secret: str = Field(default="dev-insecure-jwt-secret", alias="LAYLA_JWT_SECRET")
    jwt_ttl_minutes: int = Field(default=1440, alias="LAYLA_JWT_TTL_MINUTES")

    # --- Database ---
    database_url: str = Field(
        default="postgresql+asyncpg://layla:layla@localhost:5432/layla",
        alias="DATABASE_URL",
    )

    # --- Redis ---
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # Куда клонируются репозитории / создаются проекты (Settings -> General).
    projects_dir: str = Field(default="./data/projects", alias="LAYLA_PROJECTS_DIR")

    # Адреса анонимизирующих маршрутов для egress-проб (спец. §7.4).
    tor_addr: str = Field(default="127.0.0.1:9050", alias="LAYLA_TOR_ADDR")
    proxy_addr: str = Field(default="", alias="LAYLA_PROXY_ADDR")

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
