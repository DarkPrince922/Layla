from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str | None = Field(default=None, max_length=120)


class UserOut(BaseModel):
    id: str
    # Выходные схемы — просто str: значение уже проверено при создании, а домены
    # вроде .local (дефолтный админ) не должны ломать сериализацию ответа.
    email: str
    display_name: str | None = None
    is_admin: bool = False
    is_active: bool = True
    must_change_password: bool = False

    model_config = {"from_attributes": True}


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


class BootstrapOut(BaseModel):
    """Одноразовые учётные данные админа для окна первого входа."""

    available: bool
    email: str | None = None
    password: str | None = None


# ---- Управление пользователями (только админ) ----
class AdminUserCreate(BaseModel):
    email: EmailStr
    display_name: str | None = Field(default=None, max_length=120)
    # Пусто → пароль сгенерируется и вернётся администратору один раз.
    password: str | None = Field(default=None, min_length=8, max_length=200)
    is_admin: bool = False


class AdminUserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None
    is_admin: bool | None = None
    # true → сгенерировать новый пароль и вернуть его администратору.
    reset_password: bool | None = None


class AdminUserOut(UserOut):
    created_at: str | None = None


class AdminUserCreated(BaseModel):
    user: AdminUserOut
    # Присутствует, только если пароль был сгенерирован сервером.
    generated_password: str | None = None
