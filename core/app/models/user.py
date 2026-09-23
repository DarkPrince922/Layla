"""Users, workspaces, projects."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import Timestamps, UUIDPk


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    pw_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    # Администратор: единственный, кто может заводить/отключать пользователей.
    is_admin: Mapped[bool] = mapped_column(default=False, nullable=False)
    # Пароль выдан администратором/бутстрапом — попросить сменить при входе.
    must_change_password: Mapped[bool] = mapped_column(default=False, nullable=False)


class AdminBootstrap(UUIDPk, Timestamps, Base):
    """Одноразовые учётные данные админа, созданные при первой установке.

    Строка живёт до первого входа администратора (или смены пароля), после чего
    удаляется — пароль показывается в UI ровно один раз.
    """

    __tablename__ = "admin_bootstrap"

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)


class Workspace(UUIDPk, Timestamps, Base):
    __tablename__ = "workspaces"

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Where GitHub repos get cloned (Settings -> General "Projects directory").
    projects_dir: Mapped[str | None] = mapped_column(String(1024))


class Project(UUIDPk, Timestamps, Base):
    __tablename__ = "projects"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    repo_url: Mapped[str | None] = mapped_column(String(1024))
    path: Mapped[str | None] = mapped_column(String(1024))
    # project — проект домена «Код»; chat_workspace — рабочая папка чата
    # Дизайна/OSINT/Пентеста: в списке проектов её нет, удаляется вместе с чатом.
    kind: Mapped[str] = mapped_column(String(20), default="project", server_default="project")
    # Корзина: не пусто — проект удалён и через 7 дней будет стёрт окончательно.
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
